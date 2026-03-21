import socket

client_socket=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_address= ("127.0.0.1", 12345)
message="hi"

client_socket.sendto(message.encode(), server_address)
data,server=client_socket.recvfrom(1024)
print(f"Reponse serveur: {data.decode()}")

client_socket.close()