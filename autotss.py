from argparse import RawTextHelpFormatter, ArgumentParser
from configparser import ConfigParser
from io import TextIOWrapper
from json import dumps, loads
from os import path, makedirs
from subprocess import Popen, CalledProcessError, PIPE
from sys import exit

from dataset import connect
from requests import get


class Autotss:

    def __init__(self, user_path=None):
        self.script_path = self.get_script_path(user_path)
        self.live_firmware_api = self.get_firmware_api()
        self.database = connect('sqlite:///autotss.db')

        self.import_new_devices()
        self.check_all_devices()
        self.push_to_database()

    def import_new_devices(self):
        """ Checks devices.txt for new entries. Parses entries and
        inserts them into the devices table in our database """

        print('\nChecking devices.ini for new devices...')
        db = self.database['devices']
        new_devices = []
        num_new = 0

        # Check to make sure devices.ini exists, otherwise warn and continue without new devices
        if path.isfile('devices.ini'):
            config = ConfigParser()
            config.read('devices.ini')
            for section in config.sections():
                name = section
                identifier = config.get(section, 'identifier').replace(' ', '')
                ecid = config.get(section, 'ecid')

                try:
                    boardconfig = config.get(section, 'boardconfig').lower()
                except:
                    boardconfig = ''
                if not boardconfig:
                    boardconfig = self.get_board_config(identifier)

                new_devices.append(
                    {'deviceName': name, 'deviceID': identifier, 'boardConfig': boardconfig, 'deviceECID': ecid,
                     'blobsSaved': '[]'})
        else:
            print('Unable to find devices.ini')

        # Add only new devices to database
        for newdev in new_devices:
            if not db.find_one(deviceECID=newdev['deviceECID']):
                print('Device: [{deviceName}] ECID: [{deviceECID}] Board Config: [{boardConfig}]'.format(**newdev))
                num_new += 1
                db.insert(newdev)
        print('Added {} new devices to the database'.format(str(num_new)))

    def get_board_config(self, device_id):
        """ Using the IPSW.me API, when supplied a device identifier
        the relevant board config will be returned."""

        return self.live_firmware_api[device_id]['BoardConfig']

    def check_for_blobs(self, device_ecid, build_id):
        """ Checks against our database to see if blobs for a
        device have already been saved for a specific iOS version.
        The device is identified by a deviceECID, iOS version is
        identified by a buildID. """

        device_info = self.database['devices'].find_one(deviceECID=device_ecid)

        for entry in loads(device_info['blobsSaved']):
            if entry['buildID'] == build_id:
                return True

        return False

    def get_firmware_api(self):
        """ Taking the raw response from the IPSW.me API, process
         the response as a JSON object and remove unsigned firmware
         entries. Returns a freshly processed devices JSON containing
         only signed firmware versions. """

        headers = {'User-Agent': 'Script to automatically save shsh blobs (https://github.com/codsane/autotss)'}

        raw_response = get('https://api.ipsw.me/v2.1/firmwares.json/condensed', headers=headers)

        device_api = raw_response.json()['devices']

        ''' Rather than messing around with copies, we can loop
         through all firmware dictionary objects and append the
         signed firmware objects to a list. The original firmware
         list is then replaced with the new (signed firmware only) list.'''
        for device_id in device_api:
            signed_firmwares = []
            for firmware in device_api[device_id]['firmwares']:
                if firmware['signed']:
                    signed_firmwares.append(firmware)
            device_api[device_id]['firmwares'] = signed_firmwares

        return device_api

    def check_all_devices(self):
        """ Loop through all of our devices and grab matching
        device firmwares from the firmwareAPI. Device and
        firmware info is sent to saveBlobs(). """

        print('\nGrabbing devices from the database...')
        self.devices = [row for row in self.database['devices']]
        for device in self.devices:
            print('Device: [{deviceName}] ECID: [{deviceECID}] Board Config: [{boardConfig}]'.format(**device))
        print('Grabbed {} devices from the database'.format(len(self.devices)))

        print('\nSaving unsaved blobs for {} devices...'.format(str(len(self.devices))))
        for device in self.devices:
            for firmware in self.live_firmware_api[device['deviceID']]['firmwares']:
                self.save_blobs(device, firmware['buildid'], firmware['version'])

        print('Done saving blobs')

    def save_blobs(self, device, build_id, version_number):
        """ First, check to see if blobs have already been
        saved. If blobs have not been saved, use subprocess
        to call the tsschecker script and save blobs. After
        saving blobs, logSavedBlobs() is called to log that
        we saved the device/firmware blobs. """

        if self.check_for_blobs(device['deviceECID'], build_id):
            # print('[{0}] [{1}] {2}'.format(device['deviceID'], versionNumber, 'Blobs already saved!'))
            return

        save_path = 'blobs/' + device['deviceID'] + '/' + device['deviceECID'] + '/' + version_number + '/' + build_id
        if not path.exists(save_path):
            makedirs(save_path)

        script_arguments = [self.script_path,
                            '-d', device['deviceID'],
                            '-e', device['deviceECID'],
                            '--boardconfig', device['boardConfig'],
                            '--buildid', build_id,
                            '--save-path', save_path,
                            '-s']

        tss_call = Popen(script_arguments, stdout=PIPE)

        tss_output = []
        for line in TextIOWrapper(tss_call.stdout, encoding='utf-8'):
            tss_output.append(line.strip())

        ''' Checks console output for the `Saved shsh blobs!`
        string. While this works for now, tsschecker updates
        may break the check. It may be possible to check to
        see if the .shsh file was created and also check for
        the right file format. '''
        if 'Saved shsh blobs!' in tss_output:
            self.log_blobs_saved(device, build_id, version_number)
            print('[{0}] [{1} - {2}] {3}'.format(device['deviceName'], version_number, build_id, 'Saved shsh blobs!'))
        else:
            self.log_blobs_failed(script_arguments, save_path, tss_output)
            print('[{0}] [{1} - {2}] {3}'.format(device['deviceName'], version_number, build_id,
                                                 'Error, see log file: ' + save_path + '/tsschecker_log.txt'))

    def log_blobs_saved(self, device, build_id, version_number):
        """ Taking a reference to a device dictionary, we can
         load the string `blobsSaved` from the database into
         a JSON object, append a newly saved version, and
         turn the JSON object back into a string and
         replace `blobsSaved` """

        old_blobs_saved = loads(device['blobsSaved'])
        new_blobs_saved = {'releaseType': 'release', 'versionNumber': version_number, 'buildID': build_id}

        old_blobs_saved.append(new_blobs_saved)

        device['blobsSaved'] = dumps(old_blobs_saved)

    def log_blobs_failed(self, script_arguments, save_path, tss_output):
        """ When blobs are unable to be saved, we save
        a log of tsschecker's output in the blobs folder. """

        with open(save_path + '/tsschecker_log.txt', 'w') as file:
            file.write(' '.join(script_arguments) + '\n\n')
            file.write('\n'.join(tss_output))

    def push_to_database(self):
        """ Loop through all of our devices and update their
        entries into the database. ECID is used as the value
        to update by, as it is the only unique device identifier."""

        print('\nUpdating database with newly saved blobs...')
        for device in self.devices:
            self.database['devices'].update(device, ['deviceECID'])
        print('Done updating database')

    def get_script_path(self, user_path):
        """ Determines if the user provided a path to the tsschecker
         binary, whether command line argument or passed to autotss().
         If the user did not provide a path, try to find it within
         /tsschecker or /tsschecker-latest and select the proper binary
         Also verifies that these files exist. """

        script_path = None

        arg_parser = ArgumentParser(formatter_class=RawTextHelpFormatter)
        arg_parser.add_argument("-p", "--path",
                                help='Supply the path to your tsschecker binary.\nExample: -p /Users/codsane/tsschecker/tsschecker_macos',
                                required=False, default='')
        argument = arg_parser.parse_args()

        # Check to see if the user provided the command line argument -p or --path
        if argument.path:
            script_path = argument.path

            # Check to make sure this file exists
            if path.isfile(argument.path):
                print('Using manually specified tsschecker binary: ' + argument.path)
            else:
                print('Unable to find tsschecker at specificed path: ' + argument.path)
                exit()

        # No command line argument provided, check to see if a path was passed to autotss()
        else:
            script_path = "tsschecker"

        try:
            tss_call = Popen(script_path, stdout=PIPE)
        except CalledProcessError:
            pass
        except OSError:
            print('tsschecker not found. Install or point to with -p')
            print('Get tsschecker here: https://github.com/encounter/tsschecker/releases')
            exit()

        # Check to make sure user has the right tsschecker version
        tss_output = []
        for line in TextIOWrapper(tss_call.stdout, encoding='utf-8'):
            tss_output.append(line.strip())

        version_number = int(tss_output[0].split('-')[-1].strip())
        if version_number < 247:
            print('Your version of tss checker is too old')
            print('Get the latest version here: http://api.tihmstar.net/builds/tsschecker/tsschecker-latest.zip')
            print('Unzip into the same folder as autotss')
            exit()

        return script_path


def main():
    # autotss('/Users/codsane/tsschecker/tsschecker_macos')
    Autotss()


if __name__ == "__main__":
    main()
