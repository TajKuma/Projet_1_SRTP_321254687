import socket

server_socket=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.bind(("0.0.0.0", 12345))
print("serveur udp ecoute sur le port 12345")
while True:
    data, addr=server_socket.recvfrom(1024)
    print(f"Reçu {addr}: {data.decode()}")
    response= "message reçu"
    server_socket.sendto(response.encode(), addr)
    