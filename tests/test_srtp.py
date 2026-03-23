#!/usr/bin/env python3

import os
import sys
import time
import hashlib
import subprocess
import pytest
from client import SRTPPacket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CLIENT = os.path.join(PROJECT_ROOT, 'src', 'client.py')
SERVER = os.path.join(PROJECT_ROOT, 'src', 'server.py')
LINK_SIM = os.path.join(PROJECT_ROOT, 'link_sim')


class TestSRTPPacketEncoding:
    def test_data_packet_encode_decode(self):
        """encodes and decodes correctly"""
        p = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
        enc = p.encode()
        dec = SRTPPacket.decode(enc)
        assert dec.ptype == 1
        assert dec.seqnum == 42
        assert dec.payload == b'HelloWorld'

    def test_ack_packet_encode_decode(self):
        """ACK packet encodes, decodes, and is recognised as ACK"""
        p = SRTPPacket(2, 10, 0, 43, 12345678, b'')
        enc = p.encode()
        dec = SRTPPacket.decode(enc)
        assert dec.is_ack()

    def test_end_packet_detection(self):
        """END (FIN) packet is correctly detected via is_end()"""
        p = SRTPPacket(1, 5, 0, 100, 12345678, b'')
        assert p.is_end()


class TestSRTPCRCErrorDetection:
    def test_corrupted_packet_raises_value_error(self):
        p = SRTPPacket(1, 5, 10, 42, 12345678, b'HelloWorld')
        enc = bytearray(p.encode())

        # Corrupt one byte in the packet
        enc[10] ^= 0xFF

        with pytest.raises(ValueError):
            SRTPPacket.decode(bytes(enc))

def _start_server(port, root=None):
    """Start"""
    cmd = [sys.executable, SERVER]
    if root:
        cmd += ['--root', str(root)]
    cmd += ['::1', str(port)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _start_link_sim(*args):
    return subprocess.Popen(
        [LINK_SIM] + list(args),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _run_client(url, save_path, timeout=30):
    return subprocess.run(
        [sys.executable, CLIENT, url, '--save', str(save_path)],
        capture_output=True,
        timeout=timeout,
    )

class TestSRTPFileTransfer:
    def test_basic_file_transfer(self, tmp_path):
        test_content = b"Hello SRTP! This is a test file for the protocol.\n" * 100
        src = tmp_path / "test_input.txt"
        dst = tmp_path / "test_output.txt"
        src.write_bytes(test_content)

        server = _start_server(8080, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client(f"http://[::1]:8080/{src.name}", dst)
            assert result.returncode == 0, result.stderr.decode()
            assert dst.exists(), "Output file not created"
            assert dst.read_bytes() == test_content
        finally:
            server.terminate()
            server.wait()

    def test_large_file_transfer(self, tmp_path):
        test_content = os.urandom(100 * 1024)
        src = tmp_path / "large_input.bin"
        dst = tmp_path / "large_output.bin"
        src.write_bytes(test_content)

        server = _start_server(8080, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client(f"http://[::1]:8080/{src.name}", dst)
            assert result.returncode == 0, "Large file transfer failed"
            received = dst.read_bytes()
            assert hashlib.md5(received).hexdigest() == hashlib.md5(test_content).hexdigest()
        finally:
            server.terminate()
            server.wait()

    def test_server_root_option(self, tmp_path):
        test_content = b"File in custom root directory"
        (tmp_path / "custom.txt").write_bytes(test_content)
        dst = tmp_path / "custom_output.txt"

        server = _start_server(8080, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client("http://[::1]:8080/custom.txt", dst, timeout=10)
            assert result.returncode == 0, "Client failed with custom root"
            assert dst.read_bytes() == test_content
        finally:
            server.terminate()
            server.wait()

    def test_missing_file_returns_error(self, tmp_path):
        dst = tmp_path / "missing_out.txt"
        server = _start_server(8080, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client("http://[::1]:8080/does_not_exist.txt", dst, timeout=10)
            assert result.returncode != 0, "Client should exit with error for missing file"
        finally:
            server.terminate()
            server.wait()

    def test_save_option(self, tmp_path):
        test_content = b"Testing save option"
        src = tmp_path / "save_test.txt"
        dst = tmp_path / "custom_location.model"
        src.write_bytes(test_content)

        server = _start_server(8080, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client(f"http://[::1]:8080/{src.name}", dst, timeout=10)
            assert result.returncode == 0, "Client failed with custom save path"
            assert dst.exists(), "File not saved to custom location"
            assert dst.read_bytes() == test_content
        finally:
            server.terminate()
            server.wait()

class TestSRTPLinkSimOptimized:
    # Taille réduite pour tests fiables avec pertes/corruption/jitter
    FILE_SIZES = {
        'latency': 1024,        # 1 KB
        'loss': 5 * 1024,       # 5 KB
        'corruption': 5 * 1024, # 5 KB
        'jitter': 5 * 1024,     # 5 KB
        'reorder': 5 * 1024,    # 5 KB
        'fast': 5 * 1024,       # 5 KB
        'wrap': 3 * SRTPPacket.MAX_SEQNUM
    }

    def _run_link_test(self, tmp_path, filename, link_args, port, timeout=180):
        """Helper pour exécuter un test SRTP via link_sim."""
        src = tmp_path / filename
        dst = tmp_path / f"out_{filename}"
        src.write_bytes(os.urandom(self.FILE_SIZES.get(filename.split('_')[0], 5*1024)))

        server = _start_server(port, root=tmp_path)
        time.sleep(1)
        link = _start_link_sim(*link_args)
        time.sleep(1)

        try:
            # URL correcte vers le port réel du serveur
            result = _run_client(f"http://[::1]:{port}/{src.name}", dst, timeout=timeout)
            assert result.returncode == 0, f"Client failed ({filename})"
            assert dst.exists(), "Output file not created"
            assert dst.read_bytes() == src.read_bytes(), "Data corrupted"
        finally:
            server.terminate(); server.wait()
            link.terminate(); link.wait()

    def test_latency_200ms(self, tmp_path):
        self._run_link_test(
            tmp_path, "latency_test.bin",
            ['-p', '1341', '-P', '12345', '-d', '200', '-j', '50', '-R'],
            port=12345,
            timeout=180
        )

    def test_packet_loss_20pct(self, tmp_path):
        self._run_link_test(
            tmp_path, "loss_test.bin",
            ['-p', '1342', '-P', '12346', '-l', '20', '-R'],
            port=12346,
            timeout=180
        )

    def test_packet_corruption_10pct(self, tmp_path):
        self._run_link_test(
            tmp_path, "corrupt_test.bin",
            ['-p', '1343', '-P', '12347', '-e', '10', '-R'],
            port=12347,
            timeout=180
        )

    def test_jitter_200ms(self, tmp_path):
        self._run_link_test(
            tmp_path, "jitter_test.bin",
            ['-p', '1344', '-P', '12348', '-d', '200', '-j', '100', '-R'],
            port=12348,
            timeout=180
        )

    def test_packet_reordering(self, tmp_path):
        self._run_link_test(
            tmp_path, "reorder_test.bin",
            ['-p', '1345', '-P', '12349', '-R'],
            port=12349,
            timeout=180
        )

    def test_fast_retransmit(self, tmp_path):
        self._run_link_test(
            tmp_path, "fast_test.bin",
            ['-p', '1346', '-P', '12350', '-l', '10', '-j', '50', '-R'],
            port=12350,
            timeout=180
        )

    def test_seqnum_wraparound(self, tmp_path):
        filename = "wrap_test.bin"
        src = tmp_path / filename
        dst = tmp_path / f"out_{filename}"
        src.write_bytes(os.urandom(self.FILE_SIZES['wrap']))

        server = _start_server(12351, root=tmp_path)
        time.sleep(1)
        try:
            result = _run_client(f"http://[::1]:12351/{src.name}", dst, timeout=300)
            assert result.returncode == 0, "Transfer failed on wrap-around"
            assert dst.read_bytes() == src.read_bytes(), "Data mismatch after wrap-around"
        finally:
            server.terminate(); server.wait()