import argparse
import struct
import zlib
import threading
import socket
import sys
import time 
import urllib.parse
from collections import deque
from srtp_encode_decode import SRTPPacket

DUP_ACK_THRESHOLD = 3       # for fast retransmit


class SRTPSender:
    def __init__(self, sock: socket.socket, remote_addr, init_window=1):
        self.sock = sock
        self.remote_addr = remote_addr
        self.window_size = init_window
        self.send_buffer = {}   # seqnum -> (SRTPPacket, timestamp)
        self.next_seqnum = 0
        self.base_seqnum = 0
        self.dup_ack_count = {}  # seqnum -> count
        self.lock = threading.Lock()
        self.ack_event = threading.Event()
        self.stop_flag = False

    def send_file(self, file_path: str):
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(SRTPPacket.MAX_PAYLOAD)
                if not chunk:
                    break
                pkt = SRTPPacket(SRTPPacket.PTYPE_DATA, self.window_size, len(chunk),
                                 self.next_seqnum, int(time.time()), chunk)
                with self.lock:
                    self.send_buffer[self.next_seqnum] = (pkt, time.time())
                self._send_window()
                self.next_seqnum = (self.next_seqnum + 1) % SRTPPacket.MAX_SEQNUM

        # Send final empty DATA packet to signal EOF
        eof_pkt = SRTPPacket(SRTPPacket.PTYPE_DATA, self.window_size, 0,
                             self.next_seqnum, int(time.time()))
        with self.lock:
            self.send_buffer[self.next_seqnum] = (eof_pkt, time.time())
        self._send_window()

        # Wait until all ACKs received
        while self.send_buffer:
            time.sleep(0.01)

    def _send_window(self):
        with self.lock:
            for seq, (pkt, ts) in self.send_buffer.items():
                if seqnum_in_window(seq, self.base_seqnum, self.window_size):
                    self.sock.sendto(pkt.encode(), self.remote_addr)

    def handle_ack(self, ack_pkt: SRTPPacket):
        ack_num = ack_pkt.seqnum
        with self.lock:
            if ack_num == self.base_seqnum:
                # Duplicate ACK
                self.dup_ack_count[ack_num] = self.dup_ack_count.get(ack_num, 0) + 1
                if self.dup_ack_count[ack_num] >= DUP_ACK_THRESHOLD:
                    # fast retransmit
                    pkt, _ = self.send_buffer.get(ack_num, (None, None))
                    if pkt:
                        self.sock.sendto(pkt.encode(), self.remote_addr)
            else:
                # Slide window
                self.dup_ack_count.clear()
                to_remove = []
                for seq in self.send_buffer:
                    if seqnum_in_window(seq, self.base_seqnum, self.window_size) and seq != ack_num:
                        continue
                    if seqnum_in_window(seq, ack_num, SRTPPacket.MAX_SEQNUM):
                        to_remove.append(seq)
                for seq in to_remove:
                    self.send_buffer.pop(seq, None)
                self.base_seqnum = ack_num
                self._send_window()

class RTTEstimator:

    def __init__(self, init_rto=1.0):
        self.srtt = None
        self.rttvar= None
        self.rto= init_rto
    
    def update(self, measured_rtt:float):
        if self.srtt is None:
            self.srtt = measured_rtt
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

class SRTPClient:
    def __init__(self, save_path:str):
        self.save_path = save_path
        self.sock= None
        self.server_addr = None

        # State
        self.next_seqnum=0
        self.next_expected=0
        self.window_size=10

        # Buffers
        self.send_buffer = {}
        self.rec_buffer= {}
        self.pend_pack= {}

        # Time
        self.rtt_estimator=RTTEstimator()

        # File handling
        self.rec_data = bytearray()
        self.transfer_complete = False
        self.last_ack_sent = 0
    
    def get_timestamp(self) -> int:
        return int(time.time()*1000) & 0xFFFFFFFF
    
    def connect(self, hostname: str, port: int, path: str):

        # Create IPv6 socket
        self.sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.server_addr = (hostname, port)

        # HTTP request
        request = f"GET {path}\r\n".encode('ascii')

        # Send request as DATA
        self._send_data_packet(0,request)
        self.pend_pack[0]={'send_time': time.time(),'payload': request,'retrans_count':0}

        print(f"[CLIENT] Sent request for {path}", file=sys.stderr)

        # Receive loop
        self._receive_loop()

    def _send_data_packet(self, seqnum: int, payload: bytes, is_retransmit=False):
        # If retransmitting, update timestamp to make CRC valid
        timestamp = self.get_timestamp() if is_retransmit else int(time.time())

        packet = SRTPPacket(
            ptype=SRTPPacket.PTYPE_DATA,
            window=self.window_size,
            length=len(payload),
            seqnum=seqnum,
            timestamp=timestamp,
            payload=payload
        )

        self.sock.sendto(packet.encode(), self.server_addr)
        print(f"[DEBUG] Client sent {'retransmit' if is_retransmit else 'DATA'} seq={seqnum}", file=sys.stderr)

    def _send_ack(self, seqnum:int,data_timestamp:int):
        packet = SRTPPacket(ptype=SRTPPacket.PTYPE_ACK,window=self.window_size,length=0,seqnum=seqnum,timestamp=data_timestamp,payload=b'')
        self.sock.sendto(packet.encode(),self.server_addr)

    def _receive_loop(self):
        # Loop to handle incoming packets
        self.sock.settimeout(0.1)

        while not self.transfer_complete:
            self._check_retransmission()

            try:
                data,addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue

            try:
                packet = SRTPPacket.decode(data)
            except ValueError as e:
                print(f"[CLIENT] Invalid packet: {e}",file=sys.stderr)
                continue

            if packet.is_data():
                self._handle_data_packet(packet)
            elif packet.is_ack():
                self._handle_ack_packet(packet)
            elif packet.is_sack():
                # For now, treat SACK as regular ACK because not implemented
                print(f"[Client] Received SACK, treating as ACK", file=sys.stderr)
                self._handle_ack_packet(packet)

        with open(self.save_path,'wb') as f:
            f.write(self.rec_data)
        print(f"[Client] File saved to {self.save_path}", file=sys.stderr)

    def _handle_data_packet(self,packet: SRTPPacket):
        seqnum= packet.seqnum
        payload = packet.payload
        self.window_size=packet.window

        print(f"[DEBUG] Client received DATA seq={seqnum}, len={len(payload)}", file=sys.stderr)
        
        if packet.length == 0 and seqnum == self.next_expected:
            print(f"[CLIENT] Received last packet", file=sys.stderr)

            if len(self.rec_data) == 0:
                print("[CLIENT] File not found", file=sys.stderr)
                sys.exit(1)

            self.transfer_complete = True
            self._send_ack(seqnum + 1, packet.timestamp)
            return
        
        # Check if packet is in expected window
        if seqnum >= self.next_expected and seqnum < self.next_expected+self.window_size:
            self.rec_buffer[seqnum]=payload

            while self.next_expected in self.rec_buffer:
                self.rec_data.extend(self.rec_buffer[self.next_expected])
                del self.rec_buffer[self.next_expected]
                self.next_expected +=1

                if self.next_expected >= SRTPPacket.MAX_SEQNUM:
                    self.next_expected=0
                
            self._send_ack(self.next_expected, packet.timestamp)
        elif seqnum < self.next_expected:
            self._send_ack(self.next_expected, packet.timestamp)

        else:
            pass
    
    def _handle_ack_packet(self,packet : SRTPPacket):
        ack_seq = packet.seqnum

        for seq in list(self.pend_pack.keys()):
            if seq < ack_seq:
                packet_info = self.pend_pack[seq]
                measured_rtt = time.time() - packet_info['send_time']
                self.rtt_estimator.update(measured_rtt)
                del self.pend_pack[seq]

        self.next_seqnum = max(self.next_seqnum, ack_seq)
    
    def _check_retransmission(self):
        current_time = time.time()
        rto = self.rtt_estimator.get_rto()

        for seqnum, info in list(self.pend_pack.items()):
            if current_time - info['send_time'] > rto:
                print(f"[CLIENT] Retransmitting packet {seqnum}", file=sys.stderr)
                self._send_data_packet(seqnum, info['payload'], is_retransmit=True)
                info['send_time'] = current_time
                info['retrans_count'] += 1

                if info['retrans_count'] > 10:
                    print(f"[CLIENT] Aborting packet {seqnum}", file=sys.stderr)
                    sys.exit(1)

    def close(self):
        if self.sock:
            self.sock.close()

def seqnum_in_window(seq, base, window, max_seq=SRTPPacket.MAX_SEQNUM):
    """Check if seq is in [base, base+window) modulo max_seq"""
    if base + window < max_seq:
        return base <= seq < base + window
    else:
        # wrap-around case
        return seq >= base or seq < (base + window) % max_seq

def parse_url(url: str): 
    parsed= urllib.parse.urlparse(url)

    if parsed.scheme != 'http':
        raise ValueError({f"This {parsed.scheme} is not supported"})
    
    hostname=parsed.hostname
    port = parsed.port if parsed.port else 80
    path = parsed.path if parsed.path else '/'
    return hostname,port,path

def main():
    parser = argparse.ArgumentParser(description='SRTP Client')
    parser.add_argument('--save', dest='save_path',default='11m.model',help='Location received')
    parser.add_argument('url', help='URL of the file (http://hostname:port/path/to/file)')

    args = parser.parse_args()
    try:
        hostname,port,path = parse_url(args.url)
    except Exception as e:
        print(f"[CLIENT] Invalid URL: {e}",file=sys.stderr)
        sys.exit(1)

    client=SRTPClient(args.save_path)
    try:
        client.connect(hostname,port,path)
    except Exception as e:
        print(f"[CLIENT] Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

if __name__ == "__main__":
    main()