import socket

def encode(packet_type, window, length, seqnum):

    if packet_type not in (1, 2, 3):
        print("Sending invalid packet type")
    if not (0 <= window < 2**6):
        print("Invalid Winow value")
        pass
    if not (0 <= length <= 1024):
        print("Invalid packet length, will be ignored upon recieve")
    if not (0 <= seqnum < 2**11):
        print("Error, seqnum should be a value between 0 & 2047")
        pass

    header = 0
    header |= (packet_type & 0b11) << 30
    header |= (window & 0b111111) << 24
    header |= (length & 0x1FFF) << 11
    header |= (seqnum & 0x7FF)

    return f"{header:08X}"

def decode(header):
    """
    Returns validity, P_TYPE as a string and window, length and seqnum as integer.
    If validity is false, the packet should be ignored
    """
    header = int(header, 16)
    validity = True

    packet_type = (header >> 30) & 0b11
    window = (header >> 24) & 0b111111
    length = (header >> 11) & 0x1FFF
    seqnum = header & 0x7FF

    if packet_type == 1:
        P_TYPE = "DATA"
    elif packet_type == 2:
        P_TYPE = "ACK"
    elif packet_type == 3:
        P_TYPE = "SACK"
    else:
        P_TYPE = None
        validity = False
    
    if length > 1024:
        validity = False

    return validity, P_TYPE, window, length, seqnum

server_socket=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.bind(("0.0.0.0", 12345))
print("serveur udp ecoute sur le port 12345")
while True:
    data, addr=server_socket.recvfrom(1024)
    print(f"Reçu {addr}: {data.decode()}")
    response= "message reçu"
    server_socket.sendto(response.encode(), addr)
    