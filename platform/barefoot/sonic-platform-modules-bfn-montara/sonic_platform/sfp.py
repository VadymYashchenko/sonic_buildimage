#!/usr/bin/env python

try:
    import os
    import sys
    import time

    import tempfile
    from contextlib import contextmanager
    from copy import copy

    sys.path.append(os.path.dirname(__file__))

    from .platform_thrift_client import ThriftClient
    from .platform_thrift_client import thrift_try
    from .platform_thrift_client import pltfm_mgr_try

    from sonic_platform_base.sfp_base import SfpBase
    from sonic_platform_base.sonic_sfp.sfputilbase import SfpUtilBase
except ImportError as e:
    raise ImportError (str(e) + "- required module not found")

class SfpUtil(SfpUtilBase):
    """Platform-specific SfpUtil class"""

    PORT_START = 1
    PORT_END = 0
    PORTS_IN_BLOCK = 0
    QSFP_PORT_START = 1
    QSFP_PORT_END = 0
    EEPROM_OFFSET = 0
    QSFP_CHECK_INTERVAL = 4

    @property
    def port_start(self):
        self.update_port_info()
        return self.PORT_START

    @property
    def port_end(self):
        self.update_port_info()
        return self.PORT_END

    @property
    def qsfp_ports(self):
        self.update_port_info()
        return range(self.QSFP_PORT_START, self.PORTS_IN_BLOCK + 1)

    @property
    def port_to_eeprom_mapping(self):
        print("dependency on sysfs has been removed")
        raise Exception()

    def __init__(self):
        self.ready = False
        self.phy_port_dict = {'-1': 'system_not_ready'}
        self.phy_port_cur_state = {}
        self.qsfp_interval = self.QSFP_CHECK_INTERVAL

        SfpUtilBase.__init__(self)

    def update_port_info(self):
        def qsfp_max_port_get(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_get_max_port();

        if self.QSFP_PORT_END == 0:
            self.QSFP_PORT_END = thrift_try(qsfp_max_port_get)
            self.PORT_END = self.QSFP_PORT_END
            self.PORTS_IN_BLOCK = self.QSFP_PORT_END

    def get_presence(self, port_num):
        # Check for invalid port_num
        if port_num < self.port_start or port_num > self.port_end:
            return False

        presence = False

        def qsfp_presence_get(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_presence_get(port_num)

        try:
            presence = thrift_try(qsfp_presence_get)
        except Exception as e:
            print( e.__doc__)
            print(e.message)

        return presence

    def get_low_power_mode(self, port_num):
        # Check for invalid port_num
        if port_num < self.port_start or port_num > self.port_end:
            return False

        def qsfp_lpmode_get(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_lpmode_get(port_num)

        lpmode = thrift_try(qsfp_lpmode_get)

        return lpmode

    def set_low_power_mode(self, port_num, lpmode):
        # Check for invalid port_num
        if port_num < self.port_start or port_num > self.port_end:
            return False

        def qsfp_lpmode_set(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_lpmode_set(port_num, lpmode)

        status = thrift_try(qsfp_lpmode_set)

        return (status == 0)

    def reset(self, port_num):
        # Check for invalid port_num
        if port_num < self.port_start or port_num > self.port_end:
            return False

        def qsfp_reset(client):
            client.pltfm_mgr.pltfm_mgr_qsfp_reset(port_num, True)
            return client.pltfm_mgr.pltfm_mgr_qsfp_reset(port_num, False)

        err = thrift_try(qsfp_reset)

        return not err

    def check_transceiver_change(self):
        if not self.ready:
            return

        self.phy_port_dict = {}

        try:
            client = ThriftClient().open()
        except Exception:
            return

        # Get presence of each SFP
        for port in range(self.port_start, self.port_end + 1):
            try:
                sfp_resent = client.pltfm_mgr.pltfm_mgr_qsfp_presence_get(port)
            except Exception:
                sfp_resent = False
            sfp_state = '1' if sfp_resent else '0'

            if port in self.phy_port_cur_state:
                if self.phy_port_cur_state[port] != sfp_state:
                    self.phy_port_dict[port] = sfp_state
            else:
                self.phy_port_dict[port] = sfp_state

            # Update port current state
            self.phy_port_cur_state[port] = sfp_state

        client.close()

    def get_transceiver_change_event(self, timeout=0):
        forever = False
        if timeout == 0:
            forever = True
        elif timeout > 0:
            timeout = timeout / float(1000) # Convert to secs
        else:
            print("get_transceiver_change_event:Invalid timeout value", timeout)
            return False, {}

        while forever or timeout > 0:
            if not self.ready:
                try:
                    with ThriftClient(): pass
                except Exception:
                    pass
                else:
                    self.ready = True
                    self.phy_port_dict = {}
                    break
            elif self.qsfp_interval == 0:
                self.qsfp_interval = self.QSFP_CHECK_INTERVAL

                # Process transceiver plug-in/out event
                self.check_transceiver_change()

                # Break if tranceiver state has changed
                if bool(self.phy_port_dict):
                    break

            if timeout:
                timeout -= 1

            if self.qsfp_interval:
                self.qsfp_interval -= 1

            time.sleep(1)

        return self.ready, self.phy_port_dict

    @contextmanager
    def eeprom_action(self):
        u = copy(self)
        with tempfile.NamedTemporaryFile() as f:
            u.eeprom_path = f.name
            yield u

    def _sfp_eeprom_present(self, client_eeprompath, offset):
        return client_eeprompath and super(SfpUtil, self)._sfp_eeprom_present(client_eeprompath, offset)

    def _get_port_eeprom_path(self, port_num, devid):
        def qsfp_info_get(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_info_get(port_num)

        if self.get_presence(port_num):
            eeprom_hex = thrift_try(qsfp_info_get)
            eeprom_raw = bytearray.fromhex(eeprom_hex)
            with open(self.eeprom_path, 'wb') as eeprom_cache:
                eeprom_cache.write(eeprom_raw)
            return self.eeprom_path

        return None

class Sfp(SfpBase):
    """Platform-specific Sfp class"""

    sfputil = SfpUtil()

    @staticmethod
    def port_start():
        return Sfp.sfputil.port_start

    @staticmethod
    def port_end():
        return Sfp.sfputil.port_end

    @staticmethod
    def qsfp_ports():
        return Sfp.sfputil.qsfp_ports()

    @staticmethod
    def get_transceiver_change_event(timeout=0):
        return Sfp.sfputil.get_transceiver_change_event()

    def __init__(self, port_num):
        self.port_num = port_num
        SfpBase.__init__(self)

    def get_presence(self):
        with Sfp.sfputil.eeprom_action() as u:
            return u.get_presence(self.port_num)

    def get_lpmode(self):
        with Sfp.sfputil.eeprom_action() as u:
            return u.get_low_power_mode(self.port_num)

    def set_lpmode(self, lpmode):
        with Sfp.sfputil.eeprom_action() as u:
            return u.set_low_power_mode(self.port_num, lpmode)

    def reset(self):
        return Sfp.sfputil.reset(self.port_num)

    def get_transceiver_info(self):
        with Sfp.sfputil.eeprom_action() as u:
            return u.get_transceiver_info_dict(self.port_num)

    def get_transceiver_bulk_status(self):
        with Sfp.sfputil.eeprom_action() as u:
            return u.get_transceiver_dom_info_dict(self.port_num)

    def get_transceiver_threshold_info(self):
        with Sfp.sfputil.eeprom_action() as u:
            return u.get_transceiver_dom_threshold_info_dict(self.port_num)

    def get_change_event(self, timeout=0):
        return Sfp.get_transceiver_change_event(timeout)

    def get_model(self):
        """
        Retrieves the model number (or part number) of the device
        Returns:
            string: Model/part number of device
        """
        def qsfp_model_get(client):
            return client.pltfm_mgr.pltfm_mgr_qsfp_info_get(self.port_num)

        _, status = pltfm_mgr_try(qsfp_model_get, False)
        return status

    def get_name(self):
        """
        Retrieves the name of the device
            Returns:
            string: The name of the device
        """
        return "sfp{}".format(self.port_num)

    def get_reset_status(self):
        def get_qsfp_reset(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_reset_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_reset, False)
        return status

    def get_rx_los(self):
        def get_qsfp_rx_los(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_chan_rx_los_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_rx_los, False)
        return status

    def get_rx_power(self):
        def get_qsfp_rx_power(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_chan_rx_pwr_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_rx_power, False)
        return status

    def get_temperature(self):
        def get_qsfp_temperature(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_temperature_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_temperature, False)
        return status

    
    def get_transceiver_threshold_info(self):
        def get_qsfp_threshold(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_thresholds_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_threshold, False)
        return status

    def get_tx_bias(self):
        def get_qsfp_tx_bias(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_chan_tx_bias_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_tx_bias, False)
        return status
    
    def get_tx_fault(self):
        def get_qsfp_tx_fault(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_chan_tx_fault_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_tx_fault, False)
        return status

    def get_tx_power(self):
        def get_qsfp_tx_power(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_chan_tx_pwr_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_tx_power, False)
        return status

    def get_voltage(self):
        def get_qsfp_voltage(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_voltage_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_voltage, False)
        return status

    def get_power_override(self):
        def get_qsfp_power_override(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_pwr_override_get(self.port_num)
        _, status = pltfm_mgr_try(get_qsfp_power_override, False)
        return status

    def tx_disable(self):
        def get_qsfp_tx_disable(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_tx_is_disabled()
        _, status = pltfm_mgr_try(get_qsfp_tx_disable, False)
        return status

    def tx_disable_channel(self):
        def get_qsfp_tx_disable_channel(pltfm_mgr):
            return pltfm_mgr.pltfm_mgr_qsfp_tx_disable()
        _, status = pltfm_mgr_try(get_qsfp_tx_disable_channel, False)
        return status

    def is_replaceable(self):
        """
        Indicate whether this device is replaceable.
        Returns:
            bool: True if it is replaceable.
        """
        return True    

def sfp_list_get():
    sfp_list = []
    for index in range(Sfp.port_start(), Sfp.port_end() + 1):
        sfp_node = Sfp(index)
        sfp_list.append(sfp_node)
    return sfp_list
