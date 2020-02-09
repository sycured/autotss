from __future__ import print_function

from time import sleep
from subprocess import check_output, PIPE


# python 2
# Usage: with libimobiledevice installed, plug in device to generate
# ini entry for autotss


def deviceinfo(key, line):
    return check_output(['ideviceinfo', '-k', key, '-u', line],
                        stderr=PIPE).rstrip()


UDIDs = []
print("Waiting for devices...\n")
while 1:
    status = check_output(['idevice_id', '-l'],
                          stderr=PIPE).rstrip().split('\n')
    if status[0]:
        for line in status:
            if line not in UDIDs:
                device_name = deviceinfo('DeviceName', line)
                product_type = deviceinfo('ProductType', line)
                ecid = deviceinfo('UniqueChipID', line)
                hardware_model = deviceinfo('HardwareModel', line)

                print(f'[ {device_name} ]\n'
                      f'identifier = {product_type}\n'
                      f'ecid = {ecid}\n'
                      f'boardconfig = {hardware_model}\n')
                UDIDs.append(line)

    sleep(1)
