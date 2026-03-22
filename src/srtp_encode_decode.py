import struct
import zlib
from typing import Optional, Tuple

class SRTPPacket:
    #Types (2 bits)
    PTYPE_DATA= 1
    PTYPE_ACK= 2
    PTYPE_SACK= 3

    #Constantes
    MAX_PAYLOAD= 1024
    MAX_SEQNUM= 2048

    HEADER_FORMAT= '!I'
    TIMESTAMP_FORMAT= '!I'
    CRC1_FORMAT= '!I'

    def __init__(self, ptype: int, window: int, length: int, seqnum: int, timestamp: int, payload: bytes=b''):
        self.ptype=ptype            #(1=DATA, 2=ACK, 3=SACK)
        self.window=window          #(0-63)
        self.length=length          #(0-1024)
        self.seqnum=seqnum          #(0-2047)
        self.timestamp=timestamp    #(0-...s)
        self.payload=payload        #(max 1024 bytes)

        self._check()

    def _check(self):
        #Check if any of the packet arg is wrong
        if self.ptype not in (self.PTYPE_DATA,self.PTYPE_ACK,self.PTYPE_SACK):
            raise ValueError(f"Invalid packet type: {self.ptype}")
        if self.window<0 or self.window>63:
            raise ValueError(f"Window out of bound: {self.window}")
        if self.length<0 or self.length>self.MAX_PAYLOAD:
            raise ValueError(f"Length out of bound: {self.length}")
        if self.seqnum<0 or self.seqnum>=self.MAX_SEQNUM:
            raise ValueError(f"Seqnum out of bound: {self.seqnum}")
        if self.length != len(self.payload):
            raise ValueError(f"Length {self.length} != payload {len(self.payload)}")
    
    def _pack_header(self) -> bytes:
        #pack the header args into bytes
        header_word = (self.ptype <<30) | (self.window << 24) | (self.length << 11) | self.seqnum
        return struct.pack('!I',header_word)
    
    def encode(self) -> bytes:
        #Encode the packet into bytes ready to be send
        header_no_crc = self._pack_header()
        timestamp_bt = struct.pack('!I',self.timestamp)
        header_no_crc += timestamp_bt
        #Calculate CRC1
        crc1= zlib.crc32(header_no_crc) & 0xffffffff
        crc1_bt = struct.pack('!I',crc1)
        header = header_no_crc + crc1_bt

        if self.length>0:
            crc2 = zlib.crc32(self.payload) & 0xffffffff
            crc2_bt = struct.pack('!I',crc2)
            return header + self.payload + crc2_bt
        else:
            #If no payload
            return header
        
    @classmethod
    def decode(cls, data: bytes) -> 'SRTPPacket':
        if len(data)<12:
            raise ValueError(f"{len(data)}<12")
        
        header_word = struct.unpack('!I', data[:4])[0]

        ptype= (header_word >> 30) & 0x3
        window= (header_word >>24) & 0x3F
        length= (header_word >> 11) & 0x1FFF
        seqnum= header_word & 0x7FF

        if length > cls.MAX_PAYLOAD:
            raise ValueError(f"{length}>{cls.MAX_PAYLOAD}")
        
        timestamp= struct.unpack('!I',data[4:8])[0]
        received_crc1= struct.unpack('!I',data[8:12])[0]
        header_no_crc = data[:8]
        computed_crc1=zlib.crc32(header_no_crc) & 0xffffffff

        if received_crc1 != computed_crc1:
            raise ValueError(f"CRC1: r {received_crc1} != c {computed_crc1}")
        
        payload = b''
        if length >0:
            exp_size = 12+length+4 #header+payload+crc2
            if len(data)<exp_size:
                raise ValueError(f"Packet truncated: exp {exp_size}, got {len(data)}")
            payload= data[12:12+length]
            received_crc2 = struct.unpack('!I',data[12+length:16+length])[0]
            computed_crc2 = zlib.crc32(payload) & 0xffffffff

            if received_crc2 != computed_crc2:
                raise ValueError(f"CRC2: r {received_crc2} != c {computed_crc2}")
        return cls(ptype,window,length,seqnum,timestamp,payload)
    
    def is_data(self) -> bool:
        return self.ptype == self.PTYPE_DATA

    def is_ack(self)->bool:
        return self.ptype == self.PTYPE_ACK

    def is_sack(self) -> bool:
        return self.ptype == self.PTYPE_SACK
    
    def is_end(self) -> bool:
        return self.is_data() and self.length ==0
    
    def __repr__(self) -> str:
        return (f"SRTPPacket(type={self.ptype}, window={self.window}, "
                f"length={self.length}, seqnum={self.seqnum}, "
                f"timestamp={self.timestamp}, payload_size={len(self.payload)})")