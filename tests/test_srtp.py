#!/usr/bin/env python3
"""
Test script for SRTP implementation.
Run with: python3 -m tests.test_srtp
"""

import os
import sys
import time
import hashlib
import tempfile
import subprocess
from pathlib import Path

# Add src to path (parent directory because tests/ is a subfolder)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from srtp_encode_decode import SRTPPacket


def test_packet_encoding():
    """Test packet encoding and decoding"""
    print("\n[TEST 1] Packet encoding/decoding")
    
    # DATA packet
    p = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
    enc = p.encode()
    dec = SRTPPacket.decode(enc)
    assert dec.ptype == 1
    assert dec.seqnum == 42
    assert dec.payload == b'HelloWorld'
    print("  ✓ DATA packet")
    
    # ACK packet
    p = SRTPPacket(2, 10, 0, 43, 12345678, b'')
    enc = p.encode()
    dec = SRTPPacket.decode(enc)
    assert dec.is_ack()
    print("  ✓ ACK packet")
    
    # END packet (FIN)
    p = SRTPPacket(1, 5, 0, 100, 12345678, b'')
    assert p.is_end()
    print("  ✓ END packet detection")
    
    print("  ✅ PASSED")
    return True


def test_crc_error():
    """Test CRC error detection"""
    print("\n[TEST 2] CRC error detection")
    
    p = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
    enc = bytearray(p.encode())
    
    # Corrupt the packet
    enc[10] ^= 0xFF
    
    try:
        SRTPPacket.decode(bytes(enc))
        print("  ✗ CRC error not detected")
        return False
    except ValueError:
        print("  ✓ CRC error detected")
    
    print("  ✅ PASSED")
    return True


def test_file_transfer():
    """Test file transfer between client and server"""
    print("\n[TEST 3] File transfer")
    
    # Go to project root for file operations
    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)
    
    # Create test file
    test_content = b"Hello SRTP! This is a test file for the protocol.\n" * 100
    test_file = "test_input.txt"
    output_file = "test_output.txt"
    
    with open(test_file, "wb") as f:
        f.write(test_content)
    
    # Start server in background
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    
    # Run client
    client_proc = subprocess.run(
        [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
        capture_output=True,
        timeout=30
    )
    
    # Stop server
    server_proc.terminate()
    server_proc.wait()
    
    # Check result
    if client_proc.returncode != 0:
        print(f"  ✗ Client failed: {client_proc.stderr.decode()}")
        return False
    
    if not os.path.exists(output_file):
        print("  ✗ Output file not created")
        return False
    
    with open(output_file, "rb") as f:
        received = f.read()
    
    if received != test_content:
        print("  ✗ File content mismatch")
        return False
    
    print("  ✓ File transferred correctly")
    
    # Cleanup
    os.remove(test_file)
    os.remove(output_file)
    
    print("  ✅ PASSED")
    return True


def test_large_file():
    """Test large file transfer"""
    print("\n[TEST 4] Large file transfer")
    
    # Go to project root for file operations
    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)
    
    # Create 100KB test file
    test_content = os.urandom(100 * 1024)
    test_file = "large_input.bin"
    output_file = "large_output.bin"
    
    with open(test_file, "wb") as f:
        f.write(test_content)
    
    # Start server
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    
    # Run client
    client_proc = subprocess.run(
        [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
        capture_output=True,
        timeout=30
    )
    
    # Stop server
    server_proc.terminate()
    server_proc.wait()
    
    # Check
    if client_proc.returncode != 0:
        print("  ✗ Large file transfer failed")
        return False
    
    with open(output_file, "rb") as f:
        received = f.read()
    
    # Compare checksums
    original_hash = hashlib.md5(test_content).hexdigest()
    received_hash = hashlib.md5(received).hexdigest()
    
    if original_hash != received_hash:
        print(f"  ✗ Checksum mismatch: {original_hash} vs {received_hash}")
        return False
    
    print(f"  ✓ {len(test_content)} bytes transferred correctly")
    
    # Cleanup
    os.remove(test_file)
    os.remove(output_file)
    
    print("  ✅ PASSED")
    return True


def test_root_directory():
    """Test server --root option"""
    print("\n[TEST 5] Server --root option")
    
    # Go to project root for file operations
    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)
    
    # Create temp directory with test file
    temp_dir = tempfile.mkdtemp()
    test_content = b"File in custom root directory"
    test_file = os.path.join(temp_dir, "custom.txt")
    
    with open(test_file, "wb") as f:
        f.write(test_content)
    
    output_file = "custom_output.txt"
    
    # Start server with custom root
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "--root", temp_dir, "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    
    # Run client
    client_proc = subprocess.run(
        [sys.executable, "src/client.py", "http://[::1]:8080/custom.txt", "--save", output_file],
        capture_output=True,
        timeout=10
    )
    
    # Stop server
    server_proc.terminate()
    server_proc.wait()
    
    # Check
    if client_proc.returncode != 0:
        print("  ✗ Client failed with custom root")
        return False
    
    with open(output_file, "rb") as f:
        received = f.read()
    
    if received != test_content:
        print("  ✗ Content mismatch with custom root")
        return False
    
    # Cleanup
    os.remove(output_file)
    os.remove(test_file)
    os.rmdir(temp_dir)
    
    print("  ✓ Custom root directory works")
    print("  ✅ PASSED")
    return True


def test_missing_file():
    """Test request for missing file"""
    print("\n[TEST 6] Missing file handling")
    
    # Go to project root for file operations
    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)
    
    # Start server
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    
    # Request non-existent file
    client_proc = subprocess.run(
        [sys.executable, "src/client.py", "http://[::1]:8080/does_not_exist.txt", "--save", "missing_out.txt"],
        capture_output=True,
        timeout=10
    )
    
    # Stop server
    server_proc.terminate()
    server_proc.wait()
    
    # Check that client exits gracefully
    if client_proc.returncode != 0:
        print("  ✓ Client handles missing file correctly")
    else:
        print("  ✗ Client should exit with error for missing file")
        return False
    
    # Cleanup
    if os.path.exists("missing_out.txt"):
        os.remove("missing_out.txt")
    
    print("  ✅ PASSED")
    return True


def test_save_option():
    """Test client --save option"""
    print("\n[TEST 7] Client --save option")
    
    # Go to project root for file operations
    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)
    
    test_content = b"Testing save option"
    test_file = "save_test.txt"
    save_path = "custom_location.model"
    
    with open(test_file, "wb") as f:
        f.write(test_content)
    
    # Start server
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    
    # Run client with custom save path
    client_proc = subprocess.run(
        [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", save_path],
        capture_output=True,
        timeout=10
    )
    
    # Stop server
    server_proc.terminate()
    server_proc.wait()
    
    # Check
    if client_proc.returncode != 0:
        print("  ✗ Client failed with custom save path")
        return False
    
    if not os.path.exists(save_path):
        print("  ✗ File not saved to custom location")
        return False
    
    with open(save_path, "rb") as f:
        received = f.read()
    
    if received != test_content:
        print("  ✗ Content mismatch with custom save")
        return False
    
    # Cleanup
    os.remove(test_file)
    os.remove(save_path)
    
    print("  ✓ Custom save path works")
    print("  ✅ PASSED")
    return True

def test_srtp_latency():
    """Test SRTP transfer with 200 ms latency using link_sim, with timestamps and order check"""
    print("\n[TEST 8] SRTP transfer with 200 ms latency")

    project_root = os.path.dirname(os.path.dirname(__file__))
    os.chdir(project_root)

    test_content = b"Testing latency handling in SRTP\n"
    test_file = "latency_test.txt"
    output_file = "latency_out.txt"

    # Créer le fichier de test
    tests_dir = os.path.join(project_root, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    with open(os.path.join(tests_dir, test_file), "wb") as f:
        f.write(test_content)

    # Démarrer le serveur
    server_proc = subprocess.Popen(
        [sys.executable, "src/server.py", "::1", "12345", "--root", tests_dir],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)

    # Démarrer link_sim avec latence 200ms bidirectionnelle
    link_proc = subprocess.Popen(
        ["./link_sim", "-p", "1341", "-P", "12345", "-d", "200", "-j", "0", "-e", "0", "-c", "0", "-l", "0", "-R"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)

    # Lancer le client et capturer stdout pour les séquences
    timestamps = []
    seq_order = []

    start_time = time.time()
    with subprocess.Popen(
        [sys.executable, "src/client.py", f"http://[::1]:1341/{test_file}", "--save", output_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    ) as client_proc:
        try:
            for line in client_proc.stdout:
                print(line, end="")  # afficher debug client
                if "DEBUG] Client received DATA seq=" in line:
                    seq = int(line.split("seq=")[1].split(",")[0])
                    timestamps.append(time.time())
                    seq_order.append(seq)

            client_proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            print("  ✗ Client timed out")
            server_proc.terminate()
            link_proc.terminate()
            return False
    end_time = time.time()

    # Arrêter serveur et link_sim
    server_proc.terminate()
    server_proc.wait()
    link_proc.terminate()
    link_proc.wait()

    # Vérifier le résultat du fichier
    if client_proc.returncode != 0:
        print(f"  ✗ Client failed")
        return False
    if not os.path.exists(output_file):
        print("  ✗ Output file not created")
        return False
    with open(output_file, "rb") as f:
        received = f.read()
    if received != test_content:
        print("  ✗ File content mismatch")
        return False

    # Vérifier l'ordre des paquets
    if seq_order != sorted(seq_order):
        print("  ✗ Packets received out of order")
        return False

    # Afficher timestamps et temps total
    print(f"  ⏱ Total transfer time: {end_time - start_time:.3f} s")
    for i, t in enumerate(timestamps):
        print(f"    Packet seq={seq_order[i]} received at {t - start_time:.3f} s")

    # Cleanup
    os.remove(os.path.join(tests_dir, test_file))
    os.remove(output_file)

    print("  ✓ File transferred correctly with 200 ms latency, correct order")
    print("  ✅ PASSED")
    return True

def main():
    """Run all tests"""
    print("\n" + "="*50)
    print("SRTP IMPLEMENTATION TEST")
    print("="*50)
    
    tests = [
        ("Packet encoding/decoding", test_packet_encoding),
        ("CRC error detection", test_crc_error),
        ("File transfer", test_file_transfer),
        ("Large file transfer", test_large_file),
        ("Server --root option", test_root_directory),
        ("Missing file handling", test_missing_file),
        ("Client --save option", test_save_option),
        ("SRTP transfer with 200 ms latency", test_srtp_latency),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ EXCEPTION: {e}")
            failed += 1
    
    print("\n" + "="*50)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*50)
    
    if failed > 0:
        sys.exit(1)
    else:
        print(f"\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()