import argparse
import socket
import sys
import time
import os
from srtp_encode_decode import SRTPPacket

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