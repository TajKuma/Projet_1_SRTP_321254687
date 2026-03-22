#!/usr/bin/env python3
"""
Performance test script for SRTP implementation.
Run with: python3 test_perf.py
"""

import os
import sys
import time
import subprocess
import hashlib
import tempfile
import matplotlib.pyplot as plt
import numpy as np

# Get project root (parent of tests folder)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')

# Add src to path
sys.path.insert(0, SRC_DIR)


class PerformanceTest:
    def __init__(self):
        self.results = {
            'perfect': {'sizes': [], 'times': [], 'throughput': []},
            'latency': {'sizes': [], 'times': [], 'throughput': []},
            'loss': {'sizes': [], 'times': [], 'throughput': []}
        }
        self.server_proc = None
        
    def start_server(self, root_dir='.', latency_ms=0, loss_percent=0):
        """Start server with optional link simulation"""
        # Change to project root for file operations
        os.chdir(PROJECT_ROOT)
        
        if latency_ms > 0 or loss_percent > 0:
            if not os.path.exists(os.path.join(PROJECT_ROOT, 'link_sim')):
                print("  Warning: link_sim not found, running without simulation")
                return self._start_plain_server(root_dir)
            return self._start_simulated_server(root_dir, latency_ms, loss_percent)
        return self._start_plain_server(root_dir)
    
    def _start_plain_server(self, root_dir):
        """Start plain server"""
        server_path = os.path.join(SRC_DIR, 'server.py')
        self.server_proc = subprocess.Popen(
            [sys.executable, server_path, "--root", root_dir, "::1", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=PROJECT_ROOT
        )
        time.sleep(2)
        return 8080
    
    def _start_simulated_server(self, root_dir, latency_ms, loss_percent):
        """Start server through link simulator"""
        server_path = os.path.join(SRC_DIR, 'server.py')
        # Start server on port 8081
        self.server_proc = subprocess.Popen(
            [sys.executable, server_path, "--root", root_dir, "::1", "8081"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=PROJECT_ROOT
        )
        time.sleep(2)
        
        # Start link simulator
        link_sim_path = os.path.join(PROJECT_ROOT, 'link_sim')
        self.link_sim_proc = subprocess.Popen(
            [link_sim_path, "-p", "8080", "-P", "8081", "-l", str(latency_ms), "-d", str(loss_percent)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=PROJECT_ROOT
        )
        time.sleep(1)
        
        return 8080
    
    def stop_server(self):
        """Stop server and simulator"""
        if self.server_proc:
            self.server_proc.terminate()
            self.server_proc.wait()
        if hasattr(self, 'link_sim_proc'):
            self.link_sim_proc.terminate()
            self.link_sim_proc.wait()
        time.sleep(1)
    
    def run_transfer_test(self, size_bytes, scenario):
        """Run a single transfer test"""
        # Change to project root for file operations
        os.chdir(PROJECT_ROOT)
        
        # Create test file in project root
        test_file = f"test_{scenario}_{size_bytes}.bin"
        output_file = f"output_{scenario}_{size_bytes}.bin"
        
        with open(test_file, 'wb') as f:
            f.write(os.urandom(size_bytes))
        
        # Start server
        if scenario == 'latency':
            port = self.start_server(latency_ms=100, loss_percent=0)
        elif scenario == 'loss':
            port = self.start_server(latency_ms=0, loss_percent=5)
        else:
            port = self.start_server()
        
        # Run client and measure time
        start_time = time.time()
        
        client_path = os.path.join(SRC_DIR, 'client.py')
        client_proc = subprocess.run(
            [sys.executable, client_path, f"http://[::1]:{port}/{test_file}", "--save", output_file],
            capture_output=True,
            timeout=60,
            cwd=PROJECT_ROOT
        )
        
        end_time = time.time()
        transfer_time = end_time - start_time
        
        # Stop server
        self.stop_server()
        
        # Verify transfer
        if client_proc.returncode != 0:
            print(f"\n  Error: {client_proc.stderr.decode()[:200]}")
            # Cleanup
            if os.path.exists(test_file):
                os.remove(test_file)
            return None
        
        if not os.path.exists(output_file):
            print(f"\n  Output file not created")
            if os.path.exists(test_file):
                os.remove(test_file)
            return None
        
        with open(output_file, 'rb') as f:
            received = f.read()
        
        with open(test_file, 'rb') as f:
            original = f.read()
        
        if received != original:
            print(f"\n  Content mismatch")
            os.remove(test_file)
            os.remove(output_file)
            return None
        
        # Calculate throughput (Mbps)
        throughput = (size_bytes * 8) / (transfer_time * 1000000)
        
        # Cleanup
        os.remove(test_file)
        os.remove(output_file)
        
        return {
            'size': size_bytes,
            'time': transfer_time,
            'throughput': throughput
        }
    
    def run_all_tests(self):
        """Run all performance tests"""
        # Start with smaller file sizes for testing
        file_sizes = [1024, 5120, 10240, 51200, 102400]  # 1KB to 100KB
        scenarios = ['perfect']
        
        print("\n" + "="*60)
        print("PERFORMANCE TEST - SRTP PROTOCOL")
        print("="*60)
        
        for scenario in scenarios:
            print(f"\n[{scenario.upper()}]")
            print("-"*40)
            
            for size in file_sizes:
                size_kb = size / 1024
                print(f"  Testing {size_kb:.0f} KB...", end=' ', flush=True)
                
                result = self.run_transfer_test(size, scenario)
                
                if result:
                    self.results[scenario]['sizes'].append(size)
                    self.results[scenario]['times'].append(result['time'])
                    self.results[scenario]['throughput'].append(result['throughput'])
                    print(f"✓ {result['time']:.2f}s, {result['throughput']:.2f} Mbps")
                else:
                    print("✗ FAILED")
        
        self.print_summary()
        self.plot_results()
    
    def print_summary(self):
        """Print performance summary"""
        print("\n" + "="*60)
        print("PERFORMANCE SUMMARY")
        print("="*60)
        
        for scenario in ['perfect', 'latency', 'loss']:
            print(f"\n{scenario.upper()}:")
            if self.results[scenario]['throughput']:
                avg_throughput = np.mean(self.results[scenario]['throughput'])
                max_throughput = max(self.results[scenario]['throughput'])
                min_throughput = min(self.results[scenario]['throughput'])
                print(f"  Avg throughput: {avg_throughput:.2f} Mbps")
                print(f"  Max throughput: {max_throughput:.2f} Mbps")
                print(f"  Min throughput: {min_throughput:.2f} Mbps")
                print(f"  Total tests: {len(self.results[scenario]['sizes'])}")
            else:
                print("  No data available")
    
    def plot_results(self):
        """Generate performance graph"""
        if not self.results['perfect']['sizes']:
            print("\nNo data to plot")
            return
            
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        
        # Plot 1: Transfer time vs file size
        ax1 = axes[0]
        for scenario in ['perfect', 'latency', 'loss']:
            sizes = [s / 1024 for s in self.results[scenario]['sizes']]
            times = self.results[scenario]['times']
            if sizes and times:
                ax1.plot(sizes, times, 'o-', label=scenario.capitalize(), linewidth=2, markersize=8)
        
        ax1.set_xlabel('File Size (KB)', fontsize=12)
        ax1.set_ylabel('Transfer Time (seconds)', fontsize=12)
        ax1.set_title('Transfer Time vs File Size', fontsize=14)
        ax1.grid(True, alpha=0.3)
        if self.results['perfect']['sizes']:
            ax1.legend()
        
        # Plot 2: Throughput vs file size
        ax2 = axes[1]
        for scenario in ['perfect', 'latency', 'loss']:
            sizes = [s / 1024 for s in self.results[scenario]['sizes']]
            throughput = self.results[scenario]['throughput']
            if sizes and throughput:
                ax2.plot(sizes, throughput, 'o-', label=scenario.capitalize(), linewidth=2, markersize=8)
        
        ax2.set_xlabel('File Size (KB)', fontsize=12)
        ax2.set_ylabel('Throughput (Mbps)', fontsize=12)
        ax2.set_title('Throughput vs File Size', fontsize=14)
        ax2.grid(True, alpha=0.3)
        if self.results['perfect']['throughput']:
            ax2.legend()
        
        # Add overall title
        fig.suptitle('SRTP Protocol Performance Analysis', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        # Save figure
        output_path = os.path.join(PROJECT_ROOT, 'performance_results.png')
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\nGraph saved to: {output_path}")
        
        # Show plot
        plt.show()


def test_server_manually():
    """Quick test to verify server works"""
    print("\n[QUICK TEST] Checking if server works...")
    
    # Create test file
    test_file = os.path.join(PROJECT_ROOT, 'test_quick.txt')
    with open(test_file, 'w') as f:
        f.write("Hello")
    
    # Start server
    server_path = os.path.join(SRC_DIR, 'server.py')
    server_proc = subprocess.Popen(
        [sys.executable, server_path, "::1", "8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=PROJECT_ROOT
    )
    time.sleep(2)
    
    # Run client
    client_path = os.path.join(SRC_DIR, 'client.py')
    result = subprocess.run(
        [sys.executable, client_path, "http://[::1]:8080/test_quick.txt", "--save", "output_quick.txt"],
        capture_output=True,
        timeout=10,
        cwd=PROJECT_ROOT
    )
    
    # Cleanup
    server_proc.terminate()
    server_proc.wait()
    
    if result.returncode == 0 and os.path.exists(os.path.join(PROJECT_ROOT, 'output_quick.txt')):
        print("  ✓ Server works correctly!")
        os.remove(os.path.join(PROJECT_ROOT, 'output_quick.txt'))
        os.remove(test_file)
        return True
    else:
        print("  ✗ Server test failed")
        print(f"  Error: {result.stderr.decode()[:200]}")
        os.remove(test_file)
        return False


def main():
    print("\n" + "="*60)
    print("SRTP PERFORMANCE TEST SUITE")
    print("="*60)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Source directory: {SRC_DIR}")
    
    # Quick test to verify everything works
    if not test_server_manually():
        print("\nServer test failed. Please check your implementation.")
        print("Make sure:")
        print("  1. server.py and client.py are in src/")
        print("  2. No other process is using port 8080")
        print("  3. You have created a test file in the project root")
        sys.exit(1)
    
    print("\nThis test will measure:")
    print("  - Transfer time for different file sizes")
    print("  - Throughput in Mbps")
    print("\nScenarios:")
    print("  1. Perfect network (no latency, no loss)")
    
    input("\nPress Enter to start tests...")
    
    tester = PerformanceTest()
    tester.run_all_tests()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)