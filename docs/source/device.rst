Devices
===============================================================================================

The :class:`RPDevice <pyremoteplay.device.RPDevice>` class represents a Remote Play host / console.

Ideally, most interactions should be made using this class.

Devices are identified uniquely via it's MAC address, which will differ depending on the network interface it is using (WiFi/Ethernet).

The instance will need a valid status to be usable. This can be done with the :meth:`RPDevice.get_status() <pyremoteplay.device.RPDevice.get_status>` method.

Once the device has a valid status, actions can be performed such as connecting to a session, turning off/on the device.


Discovery
+++++++++++++++++++++++++++++++++++++++++++++

Devices can be discovered using the :meth:`RPDevice.search() <pyremoteplay.device.RPDevice.search>` method.
All devices that are discovered on the local network will be returned.


Creating Devices
+++++++++++++++++++++++++++++++++++++++++++++

Alternatively devices can be created manually. To create a device, the ip address or hostname needs to be known.

::

  from pyremoteplay import RPDevice

  device = RPDevice("192.168.86.2")
  device2 = RPDevice("my_device_hostname")


This will create a device if the hostname is valid. However, this does not mean that the device associated with the hostname is in fact a Remote Play device.

