#!/usr/bin/python3

import sys, argparse, struct, time
from socket import *
from signal import *
import binascii
import array

UDP_PORT = 2112

clients = {}

def int_handler (sig, frame):
    print ("interrupted!")
    node.stop ()
    sys.exit (0)


# -- ledSpeak lib --

# header:
#  32 bit length of payload(s)
#  32 bit crc of payload(s)
# payload(s):
#  16 bit sequence number
#  8 bit flags
#  8 bit message type
#  optional data depending on message type
#
# flags
#  0x01: additional message follows
#
# message type
#  0x00: panic/reset
#  0x01: select config          [8-bit config selector]
#  0x02: config: string length  [32-bit length]
#  0x03: config: scaling        [8-bit output divider]
#  0x04: config: step size      [32-bit step size]
#  0x05: config: step-mod %     [8-bit signed step-mod percent]
#  0x06: config: string-fx      [8-bit selector]
#  0x07: config: pixel-fx       [8-bit selector]
#  0x08: config: string option  [8-bit variable]
#  0x10: raw framebuffer        [variable length]
#  0x11: packed framebuffer
#  0x12: delta framebuffer
#  0x13: timed framebuffer
#
#  raw framebuffer: send out the SPI bus without transformation or timing
#   32 bit length (number of 32-bit words * 4)
#   32 bit raw data (eg 100 LEDs = 32bits * 100 = 400 bytes)
#  packed framebuffer: send out the SPI bus, adding headers as needed
#   32 bit length (bytes)
#   24 bit raw data (eg 100 LEDs = 24bits * 100 = 240 bytes)

def enum(**enums):
    return type('Enum', (), enums)

class ledSpeakPacket:
    def __init__ (self, verbose=False):
        self.flags = 0
        self.seq = 0
        self.MSG_TYPE = enum (PANIC=0x00,
                         CONFIG_SELECT=0x01,
                         CONFIG_LENGTH=0x02,
                         CONFIG_SCALING=0x03,
                         CONFIG_STEP_SIZE=0x04,
                         CONFIG_STEP_MOD=0x05,
                         CONFIG_STRING_FX=0x06,
                         CONFIG_PIXEL_FX=0x07,
                         CONFIG_STRING_OPTION=0x08,
                         FB_RAW=0x10,
                         FB_PACKED=0x11,
                         FB_DELTA=0x12,
                         FB_TIMED=0x13)

    def setFlags (more=False):
        if more: self.flags |= 0x01

    def calcCrc (self, data):
        return (binascii.crc32 (data))

    def packRawFrame (self, pixels):
        pixelData = self.packPixels(pixels)
        rawFrameBuffer = struct.pack('>I', len(pixelData)) + pixelData
        payLoad = struct.pack('>HBB', self.seq, self.flags, self.MSG_TYPE.FB_RAW) + rawFrameBuffer
        packetData = struct.pack('>II', len(payLoad), self.calcCrc(payLoad)) + payLoad
        self.seq += 1
        return (packetData)

    def unpack (self, packetData):
        self.rcvdLen = len (packetData)
        self.payloadLen, self.payloadCrc = struct.unpack_from('>II', packetData, 0)
        self.payloadSeq, self.flags, self.msgType = struct.unpack_from('>HBB', packetData, 8)
        self.payloadFbLen = struct.unpack_from('>I', packetData, 12)
        self.fb = self.unpackPixels (packetData[16:])
        self.localCrc = self.calcCrc (packetData[8:])

    def dump (self):
        print ("received {} bytes; indicated len = {}".format(self.rcvdLen, self.payloadLen + 8))
        print ("calculated CRC = {}; indicated CRC = {}".format(self.payloadCrc, self.localCrc))
        print ("seq number = {} flags = {} type = {}".format(self.payloadSeq, self.flags, self.msgType))
        print (self.fb)

class ledSpeakPacketWs2812 (ledSpeakPacket):
    def packPixels (self, pixels):
        packedFrame = [struct.pack ('>BBB', g,r,b) for r,g,b in pixels]
        return (b''.join(packedFrame))

    def unpackPixels (self, packedFrame):
        return ([(r, g, b) for (g, r, b) in zip(*[iter(packedFrame)]*3)])

class ledSpeakPacketP9813 (ledSpeakPacket):
    def pixelHeader (self, r, g, b):
        f_b = 0x30 & (b >> 2)
        f_g = 0x0C & (g >> 4)
        f_r = 0x03 & (r >> 6)
        return (0xff ^ f_b ^ f_g ^ f_r)

    def packPixels (self, pixels):
        packedFrame = [struct.pack ('>BBBB',
                  self.pixelHeader(r,g,b), b,g,r) for r,g,b in pixels]
        return (b''.join(packedFrame))

    def unpackPixels (self, packedFrame):
        return ([(h, r, g, b) for (h, g, r, b) in zip(*[iter(packedFrame)]*4)])

class ledSpeakNode:
    def __init__ (self, host, port, drv, verbose=False):
        self.sock = socket(AF_INET, SOCK_DGRAM)
        self.host = host
        self.port = port
        self.verbose = verbose
        self.drv = drv
        self.UDP_BUF_SIZE = 2048

        if self.drv == "p9813":
            self.packet = ledSpeakPacketP9813()
        elif self.drv == "ws2812":
            self.packet = ledSpeakPacketWs2812()
        else:
            print ("invalid driver type: " + drv)
            sys.exit(1)

    def listen (self):
        self.sock.bind (("", self.port))

    def stop (self):
        self.sock.close()

    def sendRawFrame (self, pixels):
        packetData = self.packet.packRawFrame(pixels)
        sent = self.sock.sendto(packetData, (self.host, self.port))
        if self.verbose: print(binascii.hexlify(packetData))

    def recvPacket (self):
        self.packetData, (addr, port) = self.sock.recvfrom (self.UDP_BUF_SIZE)
        return (addr, port)

    def decodePacket (self):
        self.packet.unpack (self.packetData)

    def dumpPacket (self):
        self.packet.dump ()


# -- end ledSpeak lib --

def main():
    parser = argparse.ArgumentParser (description = "LED Speak")
    parser.add_argument ('--host', '-t',
            help = "The hostname/ip to transmit to")
    parser.add_argument ('--port', '-p', type = int, default = 2112,
            help = "The UDP port to listen/transmit on")
    parser.add_argument ('--drv', '-d', default = "none",
            help = "Driver to use: ws2812, p9813, none")
    parser.add_argument ('--count', '-c', type = int, default = 1,
            help = "number of packets to transmit/receive")
    parser.add_argument ('command',
            help = "Valid receive commands: listen, log; simple, rainbow.")
    parser.add_argument ('--verbose', '-v', action = 'store_true',
            help = "Enable verbose debugging")
    args = parser.parse_args ()

    signal (SIGINT, int_handler)

    if args.command == "simple":
        if not args.host:
            print ("missing --host or -t")
            sys.exit(1)

        print ("Transmitting packets to {}:{}".format(args.host, args.port))
        node = ledSpeakNode (args.host, args.port, args.drv, verbose = args.verbose)
        framePixels = [(255,0,0), (0,255,0), (0,0,255)] # RGB
        for i in range (args.count):
            node.sendRawFrame(framePixels)
    elif args.command == "rainbow":
        print ("not implemented")
        sys.exit(1)
    elif args.command == "listen":
        print ("Listening for packets on port {}".format(args.port))
        node = ledSpeakNode ("dummy", args.port, args.drv, verbose = args.verbose)
        node.listen()

        for i in range (args.count):
            (addr, port) = node.recvPacket()
            node.decodePacket()
            node.dumpPacket()

        node.stop()

if __name__ == '__main__':
    main()
