import argparse
import threading
import socket
import sys
import time
import os
from srtp_encode_decode import SRTPPacket


class SRTPClientHandler(threading.Thread):
    def __init__(self, sock: socket.socket, client: ClientState):
        super().__init__(daemon=True)
        self.sock = sock
        self.client = client

    def run(self):
        while not self.client.stop_flag:
            self.send_window()
            self.retransmit_if_needed()
            time.sleep(0.05)

    def send_window(self):
        with self.client.lock:
            while len(self.client.pend_pack) < self.client.window_size:
                seq = self.client.base_seqnum + len(self.client.pend_pack)
                if seq >= self.client.next_seqnum:
                    break
                if seq not in self.client.pend_pack:
                    payload = self.client.send_buffer[seq]
                    self.send_data_packet(seq, payload)

    def send_data_packet(self, seqnum: int, payload: bytes):
        timestamp = int(time.time() * 1000) & 0xFFFFFFFF
        packet = SRTPPacket(
            ptype=SRTPPacket.PTYPE_DATA,
            window=self.client.window_size,
            length=len(payload),
            seqnum=seqnum,
            timestamp=timestamp,
            payload=payload
        )
        self.sock.sendto(packet.encode(), self.client.addr)
        self.client.pend_pack[seqnum] = {'send_time': time.time(), 'payload': payload}

    def retransmit_if_needed(self):
        now = time.time()
        with self.client.lock:
            for seqnum, info in list(self.client.pend_pack.items()):
                if now - info['send_time'] > self.client.rtt_estimator.get_rto():
                    self.send_data_packet(seqnum, info['payload'])
                    info['send_time'] = now
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
        self.end_sent = False
        self.lock = threading.Lock()
        self.stop_flag = False

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
        self.sock.settimeout(0.1)
        print(f"[Server] Listening on {self.hostname}:{self.port}",file=sys.stderr)
        print(f"[Server] Root directory: {self.root_dir}",file=sys.stderr)

        self._run()

    def _run(self):
        while True:
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
                client = ClientState(addr)
                self.clients[addr] = client
                handler = SRTPClientHandler(self.sock, client)
                client.handler = handler
                handler.start()
            else:
                client = self.clients[addr]

            if packet.is_data():
                self._handle_data_packet(client, packet)
            elif packet.is_ack() or packet.is_sack():
                self.handle_ack_packet(client, packet)

    def _handle_data_packet(self, client: ClientState, packet: SRTPPacket):
        if client.file_data is None:
            request = packet.payload.decode('ascii')
            print(f"[DEBUG] Server received request: '{request}'", file=sys.stderr)
            if not request.startswith("GET "):
                self._send_end(client)
                return
            path = request[4:].strip().lstrip('/')
            file_path = os.path.join(self.root_dir, path)
            try:
                with open(file_path, 'rb') as f:
                    client.file_data = f.read()
                    client.file_size = len(client.file_data)
            except FileNotFoundError:
                self._send_end(client)
                return


            offset= 0
            seqnum = 0

            while offset < client.file_size:
                chunk = client.file_data[offset:offset + SRTPPacket.MAX_PAYLOAD]
                client.send_buffer[seqnum]=chunk
                
                print(f"[DEBUG] Server added chunk seq={seqnum}, size={len(chunk)}", file=sys.stderr)

                offset += len(chunk)
                seqnum = (seqnum + 1) % SRTPPacket.MAX_SEQNUM

            client.next_seqnum = len(client.send_buffer)
            print(f"[DEBUG] Server total chunks: {client.next_seqnum}", file=sys.stderr)

        self._send_ack(client, packet.seqnum + 1, packet.timestamp)

    def handle_ack_packet(self, client: ClientState, packet: SRTPPacket):
        ack_seq = packet.seqnum
        with client.lock:
            for seq in list(client.pend_pack.keys()):
                if seq < ack_seq:
                    measured_rtt = time.time() - client.pend_pack[seq]['send_time']
                    client.rtt_estimator.update(measured_rtt)
                    del client.pend_pack[seq]
            client.base_seqnum = max(client.base_seqnum, ack_seq)
            if client.base_seqnum >= client.next_seqnum and not client.end_sent:
                self._send_end(client)

    def _send_ack(self, client: ClientState, seqnum: int, data_timestamp: int):
        print(f"[DEBUG] Server sending ACK seqnum={seqnum} to {client.addr}", file=sys.stderr)
        packet = SRTPPacket(
            ptype=SRTPPacket.PTYPE_ACK,
            window=10,
            length=0,
            seqnum=seqnum,
            timestamp=data_timestamp,
            payload=b''
        )
        self.sock.sendto(packet.encode(), client.addr)
    def _send_end(self,client:ClientState):
        timestamp= int(time.time()*1000) & 0xFFFFFFFF
        end_seqnum = client.next_seqnum

        packet = SRTPPacket(ptype= SRTPPacket.PTYPE_DATA,window=client.window_size,
                            length=0,seqnum=end_seqnum,timestamp=timestamp,payload=b'')
        self.sock.sendto(packet.encode(),client.addr)
        client.end_sent = True
        client.stop_flag = True
        print(f"[Server] Sent END to {client.addr}", file=sys.stderr)
    def stop(self):
        if self.sock:
            self.sock.close()


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