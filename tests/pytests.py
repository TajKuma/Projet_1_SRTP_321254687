#!/usr/bin/env python3
# Pytest test suite for SRTP implementation.
# Run with: python3 -m pytest tests/pytests.py -v

import os
import sys
import time
import hashlib
import tempfile
import subprocess
import pytest

# Add src to path - correction du chemin
# Comme on est dans tests/, il faut remonter d'un niveau
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from srtp_encode_decode import SRTPPacket


# ============================================================
# UNIT TESTS - Packet encoding/decoding
# ============================================================

class TestPacketEncoding:
    # Test packet encoding and decoding

    def test_data_packet(self):
        # Test DATA packet encode/decode
        original = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
        encoded = original.encode()
        decoded = SRTPPacket.decode(encoded)
        
        assert decoded.ptype == original.ptype
        assert decoded.window == original.window
        assert decoded.length == original.length
        assert decoded.seqnum == original.seqnum
        assert decoded.timestamp == original.timestamp
        assert decoded.payload == original.payload

    def test_ack_packet(self):
        # Test ACK packet encode/decode
        original = SRTPPacket(2, 10, 0, 43, 12345678, b'')
        encoded = original.encode()
        decoded = SRTPPacket.decode(encoded)
        
        assert decoded.is_ack()
        assert decoded.seqnum == 43

    def test_sack_packet(self):
        # Test SACK packet encode/decode
        original = SRTPPacket(3, 8, 0, 50, 87654321, b'')
        encoded = original.encode()
        decoded = SRTPPacket.decode(encoded)
        
        assert decoded.is_sack()

    def test_end_packet(self):
        # Test END packet (FIN) detection
        packet = SRTPPacket(1, 5, 0, 100, 99999999, b'')
        assert packet.is_end()

    def test_max_payload(self):
        # Test maximum payload size (1024 bytes)
        large_payload = b'X' * 1024
        packet = SRTPPacket(1, 5, 1024, 0, 12345678, large_payload)
        encoded = packet.encode()
        decoded = SRTPPacket.decode(encoded)
        
        assert len(decoded.payload) == 1024
        assert decoded.payload == large_payload

    def test_payload_too_large(self):
        # Test that payload > 1024 raises error
        large_payload = b'X' * 1025
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, 1025, 0, 12345678, large_payload)


class TestPacketValidation:
    # Test packet validation constraints

    def test_invalid_type(self):
        # Test invalid packet type
        with pytest.raises(ValueError):
            SRTPPacket(0, 5, 10, 42, 12345678, b'HelloWorld')
        
        with pytest.raises(ValueError):
            SRTPPacket(4, 5, 10, 42, 12345678, b'HelloWorld')

    def test_invalid_window(self):
        # Test window out of bounds
        with pytest.raises(ValueError):
            SRTPPacket(1, -1, 10, 42, 12345678, b'HelloWorld')
        
        with pytest.raises(ValueError):
            SRTPPacket(1, 64, 10, 42, 12345678, b'HelloWorld')

    def test_invalid_length(self):
        # Test length out of bounds
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, -1, 42, 12345678, b'HelloWorld')
        
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, 1025, 42, 12345678, b'X' * 1025)

    def test_invalid_seqnum(self):
        # Test seqnum out of bounds
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, 10, -1, 12345678, b'HelloWorld')
        
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, 10, 2048, 12345678, b'HelloWorld')

    def test_length_payload_mismatch(self):
        # Test length != payload size
        with pytest.raises(ValueError):
            SRTPPacket(1, 5, 10, 42, 12345678, b'Hello')


class TestCRC:
    # Test CRC error detection

    def test_crc1_error(self):
        # Test CRC1 corruption detection
        packet = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
        encoded = bytearray(packet.encode())
        
        # Corrupt header
        encoded[10] ^= 0xFF
        
        with pytest.raises(ValueError, match="CRC1"):
            SRTPPacket.decode(bytes(encoded))

    def test_crc2_error(self):
        # Test CRC2 corruption detection
        packet = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
        encoded = bytearray(packet.encode())
        
        # Corrupt payload
        encoded[15] ^= 0xFF
        
        with pytest.raises(ValueError, match="CRC2"):
            SRTPPacket.decode(bytes(encoded))

    def test_truncated_packet(self):
        # Test truncated packet detection
        packet = SRTPPacket(1, 5, 100, 42, 12345678, b'X' * 100)
        encoded = packet.encode()
        
        # Truncate packet
        truncated = encoded[:50]
        
        with pytest.raises(ValueError, match="truncated"):
            SRTPPacket.decode(truncated)


# ============================================================
# INTEGRATION TESTS - File transfer
# ============================================================

class TestFileTransfer:
    # Test file transfer between client and server

    @pytest.fixture
    def setup_server(self):
        # Start server for tests
        server_proc = subprocess.Popen(
            [sys.executable, "src/server.py", "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        time.sleep(1)
        yield server_proc
        server_proc.terminate()
        server_proc.wait()
        time.sleep(0.5)

    @pytest.fixture
    def create_test_file(self):
        # Create a test file and return its path and content
        test_file = "test_input.txt"
        content = b"Hello SRTP! This is a test file for the protocol.\n" * 10
        with open(test_file, "wb") as f:
            f.write(content)
        yield test_file, content
        if os.path.exists(test_file):
            os.remove(test_file)

    def test_small_file_transfer(self, setup_server, create_test_file):
        # Test transfer of a small file
        test_file, expected_content = create_test_file
        output_file = "test_output.txt"
        
        # Run client
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        assert client_proc.returncode == 0, f"Client failed: {client_proc.stderr.decode()}"
        
        # Check output
        assert os.path.exists(output_file), "Output file not created"
        
        with open(output_file, "rb") as f:
            received = f.read()
        
        assert received == expected_content, "Content mismatch"
        
        # Cleanup
        if os.path.exists(output_file):
            os.remove(output_file)

    def test_large_file_transfer(self, setup_server):
        # Test transfer of a large file (100KB)
        test_file = "large_input.bin"
        output_file = "large_output.bin"
        content = os.urandom(100 * 1024)
        
        with open(test_file, "wb") as f:
            f.write(content)
        
        # Run client
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=30,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        assert client_proc.returncode == 0, f"Client failed: {client_proc.stderr.decode()}"
        
        # Check output
        assert os.path.exists(output_file), "Output file not created"
        
        with open(output_file, "rb") as f:
            received = f.read()
        
        assert received == content, "Content mismatch"
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)


class TestServerOptions:
    # Test server command line options

    @pytest.fixture
    def setup_server_with_root(self):
        # Start server with custom root directory
        temp_dir = tempfile.mkdtemp()
        server_proc = subprocess.Popen(
            [sys.executable, "src/server.py", "--root", temp_dir, "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        time.sleep(1)
        yield temp_dir, server_proc
        server_proc.terminate()
        server_proc.wait()
        time.sleep(0.5)
        import shutil
        shutil.rmtree(temp_dir)

    def test_root_directory_option(self, setup_server_with_root):
        # Test server --root option
        temp_dir, server_proc = setup_server_with_root
        
        # Create test file in temp directory
        test_file = os.path.join(temp_dir, "custom.txt")
        expected_content = b"File in custom root directory"
        with open(test_file, "wb") as f:
            f.write(expected_content)
        
        output_file = "custom_output.txt"
        
        # Run client
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", "http://[::1]:8080/custom.txt", "--save", output_file],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        assert client_proc.returncode == 0
        
        with open(output_file, "rb") as f:
            received = f.read()
        
        assert received == expected_content
        
        # Cleanup
        if os.path.exists(output_file):
            os.remove(output_file)


class TestClientOptions:
    # Test client command line options

    @pytest.fixture
    def setup_server(self):
        # Start server for tests
        server_proc = subprocess.Popen(
            [sys.executable, "src/server.py", "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        time.sleep(1)
        yield server_proc
        server_proc.terminate()
        server_proc.wait()
        time.sleep(0.5)

    def test_save_option(self, setup_server):
        # Test client --save option
        test_file = "save_test.txt"
        save_path = "custom_location.model"
        expected_content = b"Testing save option"
        
        with open(test_file, "wb") as f:
            f.write(expected_content)
        
        # Run client
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", save_path],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        assert client_proc.returncode == 0
        
        with open(save_path, "rb") as f:
            received = f.read()
        
        assert received == expected_content
        
        # Cleanup
        os.remove(test_file)
        os.remove(save_path)


class TestErrorHandling:
    # Test error handling

    @pytest.fixture
    def setup_server(self):
        # Start server for tests
        server_proc = subprocess.Popen(
            [sys.executable, "src/server.py", "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        time.sleep(1)
        yield server_proc
        server_proc.terminate()
        server_proc.wait()
        time.sleep(0.5)

    def test_missing_file(self, setup_server):
        # Test request for non-existent file
        output_file = "missing_out.txt"
        
        # Run client
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", "http://[::1]:8080/does_not_exist.txt", "--save", output_file],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        # Client should exit with error
        assert client_proc.returncode != 0
        
        # Cleanup
        if os.path.exists(output_file):
            os.remove(output_file)

    def test_invalid_url(self):
        # Test invalid URL
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", "invalid_url"],
            capture_output=True,
            timeout=5,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        assert client_proc.returncode != 0


# ============================================================
# PERFORMANCE TESTS
# ============================================================

class TestPerformance:
    # Performance tests

    @pytest.fixture
    def setup_server(self):
        # Start server for tests
        server_proc = subprocess.Popen(
            [sys.executable, "src/server.py", "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        time.sleep(1)
        yield server_proc
        server_proc.terminate()
        server_proc.wait()
        time.sleep(0.5)

    def test_throughput_1kb(self, setup_server):
        # Test throughput for 1KB file
        test_file = "perf_1kb.bin"
        output_file = "perf_output.bin"
        content = os.urandom(1024)
        
        with open(test_file, "wb") as f:
            f.write(content)
        
        start_time = time.time()
        
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        end_time = time.time()
        
        assert client_proc.returncode == 0
        
        transfer_time = end_time - start_time
        throughput = (1024 * 8) / (transfer_time * 1000000)
        
        print(f"\n  1KB transfer: {transfer_time:.3f}s, {throughput:.2f} Mbps")
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)

    def test_throughput_10kb(self, setup_server):
        # Test throughput for 10KB file
        test_file = "perf_10kb.bin"
        output_file = "perf_output.bin"
        content = os.urandom(10 * 1024)
        
        with open(test_file, "wb") as f:
            f.write(content)
        
        start_time = time.time()
        
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        end_time = time.time()
        
        assert client_proc.returncode == 0
        
        transfer_time = end_time - start_time
        throughput = (10 * 1024 * 8) / (transfer_time * 1000000)
        
        print(f"\n  10KB transfer: {transfer_time:.3f}s, {throughput:.2f} Mbps")
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)

    def test_throughput_100kb(self, setup_server):
        # Test throughput for 100KB file
        test_file = "perf_100kb.bin"
        output_file = "perf_output.bin"
        content = os.urandom(100 * 1024)
        
        with open(test_file, "wb") as f:
            f.write(content)
        
        start_time = time.time()
        
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=30,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        end_time = time.time()
        
        assert client_proc.returncode == 0
        
        transfer_time = end_time - start_time
        throughput = (100 * 1024 * 8) / (transfer_time * 1000000)
        
        print(f"\n  100KB transfer: {transfer_time:.3f}s, {throughput:.2f} Mbps")
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)

    def test_throughput_500kb(self, setup_server):
        # Test throughput for 500KB file
        test_file = "perf_500kb.bin"
        output_file = "perf_output.bin"
        content = os.urandom(500 * 1024)
        
        with open(test_file, "wb") as f:
            f.write(content)
        
        start_time = time.time()
        
        client_proc = subprocess.run(
            [sys.executable, "src/client.py", f"http://[::1]:8080/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=60,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        end_time = time.time()
        
        assert client_proc.returncode == 0
        
        transfer_time = end_time - start_time
        throughput = (500 * 1024 * 8) / (transfer_time * 1000000)
        
        print(f"\n  500KB transfer: {transfer_time:.3f}s, {throughput:.2f} Mbps")
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)


# ============================================================
# Run tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])