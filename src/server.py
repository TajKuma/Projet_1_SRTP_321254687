import argparse
import struct
import zlib
import threading
import socket
import sys
import time
import os

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

class SRTPReceiver:
    def __init__(self, sock: socket.socket, app_callback, init_window=64):
        self.sock = sock
        self.app_callback = app_callback
        self.recv_buffer = {}  # seqnum -> SRTPPacket
        self.expected_seqnum = 0
        self.window_size = init_window
        self.last_ack_sent = -1
        self.lock = threading.Lock()
        self.stop_flag = False

    def start(self):
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        while not self.stop_flag:
            try:
                data, addr = self.sock.recvfrom(1500)
                try:
                    pkt = SRTPPacket.decode(data)
                except ValueError:
                    continue  # corrupted/truncated, ignore

                if pkt.is_data():
                    self._process_data(pkt, addr)
            except socket.timeout:
                continue

    def _process_data(self, pkt: SRTPPacket, addr):
        seq = pkt.seqnum
        with self.lock:
            if not seqnum_in_window(seq, self.expected_seqnum, self.window_size):
                # Outside window, ignore
                return

            # Buffer packet if not duplicate
            if seq not in self.recv_buffer:
                if pkt.length > 0:
                    self.recv_buffer[seq] = pkt
                elif pkt.is_end():
                    # EOF
                    self.recv_buffer[seq] = pkt

            # Deliver in-order packets to application
            while self.expected_seqnum in self.recv_buffer:
                p = self.recv_buffer.pop(self.expected_seqnum)
                if p.length > 0:
                    self.app_callback(p.payload)
                self.expected_seqnum = (self.expected_seqnum + 1) % SRTPPacket.MAX_SEQNUM

            # Send cumulative ACK
            ack_pkt = SRTPPacket(SRTPPacket.PTYPE_ACK, self.window_size,
                                 0, self.expected_seqnum, pkt.timestamp)
            self.sock.sendto(ack_pkt.encode(), addr)
            self.last_ack_sent = self.expected_seqnum
            
class RTTEstimator:

    def __init__(self,init_rto=1.0):
        self.srtt = None
        self.rttvar= None
        self.rto=init_rto

    def update(self, measured_rtt:float):

        if self.srtt is None:
            self.srtt=measured_rtt
            self.rttvar=measured_rtt/2
        else:
            alpha= 0.125
            beta= 0.25
            diff = measured_rtt - self.srtt
            self.srtt += alpha * diff
            self.rttvar += beta * (abs(diff)-self.rttvar)
        self.rto = self.srtt+4*self.rttvar
        self.rto= max(0.2,min(2.0,self.rto))
    
    def get_rto(self) ->float:
        return self.rto
    
class ClientState:

    def __init__(self, addr):
        self.addr= addr
        self.file_data= None
        self.file_size=0
        self.next_seqnum=0
        self.base_seqnum=0
        self.window_size=10
        self.send_buffer={}
        self.pend_pack={}
        self.rtt_estimator= RTTEstimator()
        self.trans_active = True
        self.end_sent = False
        self.last_ack_received=0
        self.last_timestamp=0

class SRTPFileServer:
    def __init__(self,hostname:str,port:int,root_dir:str):
        self.hostname =hostname
        self.port=port
        self.root_dir=root_dir
        self.sock=None
        self.clients = {}

    def start(self):
        # Start the server
        self.sock = socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
        self.sock.bind((self.hostname,self.port))

        print(f"[Server] Listening on {self.hostname}:{self.port}",file=sys.stderr)
        print(f"[Server] Root directory: {self.root_dir}",file=sys.stderr)

        self._run()

    def _run(self):
        self.sock.settimeout(0.1)

        while True:
            self._check_all_retransmissions()

            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue

            try:
                packet = SRTPPacket.decode(data)
            except ValueError as e:
                print(f"[Server] Invalid packet from {addr}: {e}",file=sys.stderr)
                continue

            if addr not in self.clients:
                self.clients[addr]= ClientState(addr)

            if packet.is_data():
                self._handle_data_packet(addr,packet)
            elif packet.is_ack():
                self._handle_ack_packet(addr,packet)
            elif packet.is_sack():
                # Treated as ACK for the moment
                self._handle_ack_packet(addr,packet)
            
    def _handle_data_packet(self, addr, packet: SRTPPacket):
        client = self.clients[addr]

        try:
            request = packet.payload.decode('ascii')
            print(f"[DEBUG] Server received request: '{request}'", file=sys.stderr)
        except UnicodeDecodeError:
            print(f"[SERVER] Invalid request from {addr}", file=sys.stderr)
            self._send_error_close(addr, packet)
            return
        
        # Get the HTTP 0.9 path
        if request.startswith('GET '):
            path = request[4:].strip()
            # Remove the \r\n if present
            path = path.replace('\r', '').replace('\n', '')
            # Remove the initial '/' if it exists
            if path.startswith('/'):
                path = path[1:]
            print(f"[DEBUG] Server parsed path: '{path}'", file=sys.stderr)
        else:
            print(f"[SERVER] Not a GET request", file=sys.stderr)
            self._send_error_close(addr, packet)
            return
        
        file_path = os.path.join(self.root_dir, path)
        print(f"[DEBUG] Full file path: '{file_path}'", file=sys.stderr)

        self._send_ack(addr, packet.seqnum + 1, packet.timestamp)

        try:
            with open(file_path, 'rb') as f:
                client.file_data = f.read()
                client.file_size = len(client.file_data)

            print(f"[SERVER] Serving {file_path} ({client.file_size} bytes) to {addr}", file=sys.stderr)
            
            self._send_file_data(addr)
        except FileNotFoundError:
            print(f"[Server] File not Found: {file_path}", file=sys.stderr)
            self._send_end(addr)
            del self.clients[addr]

    def _send_file_data(self, addr):
        client =self.clients[addr]

        offset=0
        seqnum = 0

        while offset < client.file_size:
            chunk = client.file_data[offset:offset + SRTPPacket.MAX_PAYLOAD]
            client.send_buffer[seqnum]=chunk

            print(f"[DEBUG] Server added chunk seq={seqnum}, size={len(chunk)}", file=sys.stderr)
            
            offset += len(chunk)
            seqnum += 1

            if seqnum >= SRTPPacket.MAX_SEQNUM:
                seqnum=0
            
        client.next_seqnum = len(client.send_buffer)

        print(f"[DEBUG] Server total chunks: {client.next_seqnum}", file=sys.stderr)
        
        self._send_window(addr)

    def _send_window(self, addr):
        client = self.clients[addr]

        while len(client.pend_pack) < client.window_size:
            seq = client.base_seqnum + len(client.pend_pack)

            if seq >= client.next_seqnum:
                break

            if seq not in client.pend_pack:
                self._send_data_packet(addr, seq, client.send_buffer[seq])

    def _send_data_packet(self,addr,seqnum:int,payload:bytes):
        client=self.clients[addr]

        timestamp= int(time.time()*1000) & 0xFFFFFFFF

        packet = SRTPPacket(ptype=SRTPPacket.PTYPE_DATA,window=client.window_size,length=len(payload),seqnum=seqnum,timestamp=timestamp,payload=payload)
        self.sock.sendto(packet.encode(),addr)

        client.pend_pack[seqnum]={'send_time': time.time(),'payload':payload}

    def _send_ack(self, addr, seqnum: int, data_timestamp: int):
        print(f"[DEBUG] Server sending ACK seqnum={seqnum} to {addr}", file=sys.stderr)
        packet = SRTPPacket(
            ptype=SRTPPacket.PTYPE_ACK,
            window=10,
            length=0,
            seqnum=seqnum,
            timestamp=data_timestamp,
            payload=b''
        )
        self.sock.sendto(packet.encode(), addr)
    def _send_end(self,addr):
        client= self.clients.get(addr)
        if not client:
            return
        
        timestamp= int(time.time()*1000) & 0xFFFFFFFF
        end_seqnum = client.next_seqnum

        packet = SRTPPacket(ptype= SRTPPacket.PTYPE_DATA,window=client.window_size,
                            length=0,seqnum=end_seqnum,timestamp=timestamp,payload=b'')
        self.sock.sendto(packet.encode(),addr)
        client.end_sent = True
        print(f"[SERVER] Sent END to {addr}",file=sys.stderr)

    def _handle_ack_packet(self, addr, packet: SRTPPacket):
        client = self.clients.get(addr)
        if not client:
            return
        
        ack_seq = packet.seqnum
        print(f"[DEBUG] Server received ACK up to seq={ack_seq}", file=sys.stderr)

        for seq in list(client.pend_pack.keys()):
            if seq < ack_seq:
                packet_info = client.pend_pack[seq]
                measured_rtt = time.time() - packet_info['send_time']
                client.rtt_estimator.update(measured_rtt)
                del client.pend_pack[seq]

        client.base_seqnum = max(client.base_seqnum, ack_seq)

        self._send_window(addr)

        # Check if all DATA have been sent and acknowledged
        all_data_sent = client.base_seqnum >= client.next_seqnum
        all_data_acked = not client.pend_pack
        
        # If all DATA are sent and acknowledged, and we haven't sent END yet
        if all_data_sent and all_data_acked and not client.end_sent:
            print(f"[SERVER] All data acked, sending END to {addr}", file=sys.stderr)
            self._send_end(addr)
        
        # If END has been sent and everything is acknowledged, terminate
        if client.end_sent and not client.pend_pack and client.base_seqnum >= client.next_seqnum:
            print(f"[SERVER] Transfer complete for {addr}", file=sys.stderr)
            del self.clients[addr]

    def _check_all_retransmissions(self):
        current_time = time.time()

        for addr, client in list(self.clients.items()):
            for seqnum, info in list(client.pend_pack.items()):
                rto = client.rtt_estimator.get_rto()
                if current_time - info['send_time'] > rto:
                    print(f"[SERVER] Retransmitting packet {seqnum} to {addr}",file=sys.stderr)
                    self._send_data_packet(addr,seqnum,info['payload'])
                    info['send_time']=current_time
    
    def _send_error_close(self,addr, packet: SRTPPacket):
        self._send_end(addr)
        if addr in self.clients:
            del self.clients[addr] 
    
    def stop(self):
        if self.sock:
            self.sock.close()

def seqnum_in_window(seq, base, window, max_seq=SRTPPacket.MAX_SEQNUM):
    """Check if seq is in [base, base+window) modulo max_seq"""
    if base + window < max_seq:
        return base <= seq < base + window
    else:
        # wrap-around case
        return seq >= base or seq < (base + window) % max_seq

def main():
    parser = argparse.ArgumentParser(description="SRTP Server")
    parser.add_argument('--root', dest='root_dir',default='.', help='Root directory to serve files')
    parser.add_argument('hostname',help='Hostname or IPv6 address to listen on')
    parser.add_argument('port',type=int,help='UDP port to listen on')

    args = parser.parse_args()

    server = SRTPFileServer(args.hostname,args.port,args.root_dir)

    try:
        server.start()
    except KeyboardInterrupt:
        print(f"\n[SERVER] Shutting down...",file=sys.stderr)
    finally:
        server.stop()

if __name__ == '__main__':
    main()