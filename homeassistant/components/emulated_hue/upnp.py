"""Provides a UPNP discovery method that mimics Hue hubs."""
import threading
import socket
import logging
import select

from aiohttp import web

from homeassistant import core
from homeassistant.components.http import HomeAssistantView

_LOGGER = logging.getLogger(__name__)


BRIDGE_FRIENDLY_NAME_TEMPLATE = 'HASS Bridge ({c.advertise_ip})'
# BRIDGE_FRIENDLY_NAME_TEMPLATE = 'Philips hue'


class DescriptionXmlView(HomeAssistantView):
    """Handles requests for the description.xml file."""

    url = '/description.xml'
    name = 'description:xml'
    requires_auth = False

    def __init__(self, config):
        """Initialize the instance of the view."""
        self.config = config

    @core.callback
    def get(self, request):
        """Handle a GET request."""
        xml_template = """<?xml version="1.0" encoding="UTF-8" ?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
<specVersion>
<major>1</major>
<minor>0</minor>
</specVersion>
<URLBase>http://{c.advertise_ip}:{c.advertise_port}/</URLBase>
<device>
<deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
<friendlyName>{friendly_name}</friendlyName>
<manufacturer>Royal Philips Electronics</manufacturer>
<manufacturerURL>http://www.philips.com</manufacturerURL>
<modelDescription>Philips hue Personal Wireless Lighting</modelDescription>
<modelName>Philips hue bridge 2015</modelName>
<modelNumber>BSB002</modelNumber>
<modelURL>http://www.meethue.com</modelURL>
<serialNumber>1234</serialNumber>
<UDN>uuid:2f402f80-da50-11e1-9b23-001788255acc</UDN>
</device>
</root>
"""

        friendly_name = BRIDGE_FRIENDLY_NAME_TEMPLATE.format(c=self.config)

        resp_text = xml_template.format(c=self.config, friendly_name=friendly_name)

        return web.Response(text=resp_text, content_type='text/xml')


class HueConfigView(HomeAssistantView):
    """Handle requests to get the configuration for the emulated hue bridge."""

    url = '/api/nouser/config'
    name = 'api:nouser:config'
    requires_auth = False

    def __init__(self, config):
        """Initialize the instance of the view."""
        self.config = config

    def gen_config(self):
        mac = '04:f0:21:24:28:28'

        friendly_name = BRIDGE_FRIENDLY_NAME_TEMPLATE.format(c=self.config)

        cfg = dict(
            name=friendly_name,
            mac=mac,
            bridgeid=mac.upper().replace(':', ''),
            modelid='BSB002',

            swversion="81012917",
            portalservice=False,
            linkbutton=True,
            dhcp=True,
            ipaddress=self.config.advertise_ip,
            netmask='255.255.255.0',
            gateway='192.168.20.254',
            apiversion="1.3.0",
            # TODO: send this as "utc" and "localtime" as timezone corrected utc
            # localtime=datetime.now(),
            # TODO: take this from the settings, once we have spiffs support
            timezone='America/Los_Angeles',
            whitelist=dict(
                api=dict(name='clientname#devicename'),
            ),
            swupdate=dict(
                text='',
                notify=False,  # Otherwise client app shows update notice
                updatestate=0,
                url='',
            ),
        )

        return cfg

    @core.callback
    def get(self, request):
        """Handle a GET request."""
        config = self.gen_config()
        return self.json(config)


class UPNPResponderThread(threading.Thread):
    """Handle responding to UPNP/SSDP discovery requests."""

    _interrupted = False

    def __init__(self, host_ip_addr, listen_port, upnp_bind_multicast,
                 advertise_ip, advertise_port):
        """Initialize the class."""
        threading.Thread.__init__(self)

        self.host_ip_addr = host_ip_addr
        self.listen_port = listen_port
        self.upnp_bind_multicast = upnp_bind_multicast

        # Note that the double newline at the end of
        # this string is required per the SSDP spec
        resp_template = """HTTP/1.1 200 OK
CACHE-CONTROL: max-age=60
EXT:
LOCATION: http://{0}:{1}/description.xml
SERVER: FreeRTOS/6.0.5, UPnP/1.0, IpBridge/0.1
hue-bridgeid: 1234
ST: urn:schemas-upnp-org:device:basic:1
USN: uuid:Socket-1_0-221438K0100073::urn:schemas-upnp-org:device:basic:1

"""

        self.upnp_response = resp_template.format(
            advertise_ip, advertise_port).replace("\n", "\r\n") \
                                         .encode('utf-8')

    def run(self):
        """Run the server."""
        # Listen for UDP port 1900 packets sent to SSDP multicast address
        ssdp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ssdp_socket.setblocking(False)

        # Required for receiving multicast
        ssdp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        ssdp_socket.setsockopt(
            socket.SOL_IP,
            socket.IP_MULTICAST_IF,
            socket.inet_aton(self.host_ip_addr))

        ssdp_socket.setsockopt(
            socket.SOL_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton("239.255.255.250") +
            socket.inet_aton(self.host_ip_addr))

        if self.upnp_bind_multicast:
            ssdp_socket.bind(("", 1900))
        else:
            ssdp_socket.bind((self.host_ip_addr, 1900))

        while True:
            if self._interrupted:
                clean_socket_close(ssdp_socket)
                return

            try:
                read, _, _ = select.select(
                    [ssdp_socket], [],
                    [ssdp_socket], 2)

                if ssdp_socket in read:
                    data, addr = ssdp_socket.recvfrom(1024)
                else:
                    # most likely the timeout, so check for interrupt
                    continue
            except socket.error as ex:
                if self._interrupted:
                    clean_socket_close(ssdp_socket)
                    return

                _LOGGER.error("UPNP Responder socket exception occurred: %s",
                              ex.__str__)
                # without the following continue, a second exception occurs
                # because the data object has not been initialized
                continue

            if "M-SEARCH" in data.decode('utf-8', errors='ignore'):
                # SSDP M-SEARCH method received, respond to it with our info
                resp_socket = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM)

                resp_socket.sendto(self.upnp_response, addr)
                resp_socket.close()

    def stop(self):
        """Stop the server."""
        # Request for server
        self._interrupted = True
        self.join()


def clean_socket_close(sock):
    """Close a socket connection and logs its closure."""
    _LOGGER.info("UPNP responder shutting down.")

    sock.close()
