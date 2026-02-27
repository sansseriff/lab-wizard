

# to be used when insturments are not found on the usb port or GPIB number they are expected to be on.
# This script just searches through the connected usb devices and looks for the Prologix controller.
# Then it searches through all the possible GPIB numbers and identifies connected insturments.


import sys

#temporary!
sys.path.append("/home/elsa/src/snspd-measure/snspd_measure/inst")


#from inst.serialInst import serialInst
from inst.serialInst import GPIBmodule
import serial.tools.list_ports
import serial
import argparse



def searchIDN(verbose=True):

    devices = serial.tools.list_ports.comports()

    controllers = [] #there can be more than one Prologix controller connected

    for item in devices:

        if item.description == 'Prologix GPIB-USB Controller':
            if verbose: print('found a controller:', item.device)
            controllers.append(item)

    instruments = {}
    #tempslot = 1

    for item in controllers:

        for GPIB in range(30):
            generic = GPIBmodule(item.device, GPIB, timeout=0.1)
            generic.connect(IDNinfo = False)
            response = generic.getIDN()
            if response == '':
                continue
            elif response.startswith('ANDO'):
                if verbose:
                    print('ANDO found')
                    print('    USB port:', item.device)
                    print('    ANDO GPIB is', GPIB)
                instruments["ANDO"] = {"USB": item.device, "GPIB": GPIB}

            elif response.startswith('Agilent Technologies,53220A'):
                if verbose:
                    print('counter found')
                    print('    USB port:', item.device)
                    print('    counter GPIB is', GPIB)
                instruments["COUNTER"] = {"USB": item.device, "GPIB": GPIB}

            elif response.startswith('Stanford_Research_Systems,SIM900'):
                if verbose:
                    print('SRS found')
                    print('    USB port:', item.device)
                    print('    SRS GPIB is', GPIB)
                instruments["SRS"] = {"USB": item.device, "GPIB": GPIB}

            else:
                if verbose:
                    print(response)
                    print('    USB port:', item.device)
                    print('    GPIB is', GPIB)
                instruments["thing"] = {"USB": item.device, "GPIB": GPIB}

    return instruments



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='USB and GPIB insturment identifier')

    args=parser.parse_args()

    searchIDN()

