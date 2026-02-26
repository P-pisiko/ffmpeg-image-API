import requests
import time
import random
import os
import threading
import statistics
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import argparse
import logging


logging.basicConfig(
    filename="Server_errors.log",
    level=logging.ERROR,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

class BenchmarkStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.response_times = []
        self.success_count = 0
        self.error_count = 0
        self.errors = defaultdict(int)
        self.start_time = None
        self.end_time = None
        
    def add_result(self, response_time, success, error_msg=None):
        with self.lock:
            self.response_times.append(response_time)
            if success:
                self.success_count += 1
            else:
                self.error_count += 1
                if error_msg:
                    self.errors[error_msg] += 1
    
    def get_stats(self):
        with self.lock:
            if not self.response_times:
                return None
            
            sorted_times = sorted(self.response_times)
            total_requests = len(self.response_times)
            
            duration = (self.end_time - self.start_time) if self.end_time else 0
            
            return {
                'total_requests': total_requests,
                'successful': self.success_count,
                'failed': self.error_count,
                'success_rate': (self.success_count / total_requests * 100) if total_requests > 0 else 0,
                'duration': duration,
                'requests_per_second': total_requests / duration if duration > 0 else 0,
                'response_times': {
                    'min': min(sorted_times),
                    'max': max(sorted_times),
                    'mean': statistics.mean(sorted_times),
                    'median': statistics.median(sorted_times),
                    'p95': sorted_times[int(len(sorted_times) * 0.95)] if sorted_times else 0,
                    'p99': sorted_times[int(len(sorted_times) * 0.99)] if sorted_times else 0,
                },
                'errors': dict(self.errors)
            }


def send_request(image_path, api_url, output_format, stats):
    filename = os.path.basename(image_path)

    try:
        with open(image_path, 'rb') as f:
            files = {'file': f}
            data = {'format': output_format}
            
            start = time.time()
            response = requests.post(api_url, files=files, data=data, timeout=30)
            elapsed = time.time() - start
            
            if response.status_code == 200:
                stats.add_result(elapsed, True)
                return True
            else:
                error_msg = f"HTTP {response.status_code}"
                try:
                    error_detail = response.json().get('error', '')
                    if error_detail:
                        error_msg += f": {error_detail}"
                except:
                    pass

                stats.add_result(elapsed, False, error_msg)

                logging.error(f"File: {filename} | Error: {error_msg}")

                return False
                
    except requests.exceptions.Timeout:
        stats.add_result(30, False, "Timeout")
        logging.error(f"File: {filename} | Error: Timeout")
        return False

    except Exception as e:
        err_name = str(type(e).__name__)
        stats.add_result(0, False, err_name)
        logging.error(f"File: {filename} | Error: {err_name}")
        return False


def worker_thread(image_files, api_url, output_format, stats, stop_event, delay_between_requests):
    """Worker thread that continuously sends requests"""
    while not stop_event.is_set():
        image_path = random.choice(image_files)
        send_request(image_path, api_url, output_format, stats)
        
        if delay_between_requests > 0:
            time.sleep(delay_between_requests)


def print_stats(stats, phase_name=""):
    data = stats.get_stats()
    if not data:
        print(f"\n{phase_name}: No data yet")
        return
    
    print(f"\n{'='*60}")
    print(f"{phase_name}")
    print(f"{'='*60}")
    print(f"Total Requests:     {data['total_requests']}")
    print(f"Successful:         {data['successful']} ({data['success_rate']:.2f}%)")
    print(f"Failed:             {data['failed']}")
    print(f"Duration:           {data['duration']:.2f}s")
    print(f"Throughput:         {data['requests_per_second']:.2f} req/s")
    print(f"\nResponse Times (seconds):")
    print(f"  Min:              {data['response_times']['min']:.3f}")
    print(f"  Mean:             {data['response_times']['mean']:.3f}")
    print(f"  Median:           {data['response_times']['median']:.3f}")
    print(f"  95th percentile:  {data['response_times']['p95']:.3f}")
    print(f"  99th percentile:  {data['response_times']['p99']:.3f}")
    print(f"  Max:              {data['response_times']['max']:.3f}")
    


def run_benchmark(
    image_dir,
    api_url="http://192.168.218.128:5000/convert/",
    output_format="jpg",
    initial_threads=1,
    max_threads=10,
    ramp_duration=30,
    sustain_duration=60,
    ramp_step=1
):
    """
    Run benchmark with ramping load
    
    Args:
        image_dir: Directory containing AVIF images
        api_url: API endpoint
        output_format: Target format (jpg, png, webp, avif)
        initial_threads: Starting number of concurrent threads
        max_threads: Maximum number of concurrent threads
        ramp_duration: Time to ramp from initial to max threads (seconds)
        sustain_duration: Time to sustain max load (seconds)
        ramp_step: How many threads to add at each ramp step
    """
    
    # Find all AVIF images
    image_files = list(Path(image_dir).glob("*.avif"))
    
    if not image_files:
        print(f"Error: No AVIF images found in {image_dir}")
        return
    
    print(f"Found {len(image_files)} AVIF images")
    print(f"API URL: {api_url}")
    print(f"Output format: {output_format}")
    print(f"Load profile: {initial_threads} -> {max_threads} threads over {ramp_duration}s, sustain for {sustain_duration}s")
    print(f"\nStarting benchmark...\n")
    
    stats = BenchmarkStats()
    stop_event = threading.Event()
    active_threads = []
    
    stats.start_time = time.time()
    
    try:
        # Phase 1: Ramp up
        print(f"PHASE 1: Ramping up from {initial_threads} to {max_threads} threads")
        
        # Start initial threads
        for i in range(initial_threads):
            t = threading.Thread(
                target=worker_thread,
                args=(image_files, api_url, output_format, stats, stop_event, 0),
                daemon=True
            )
            t.start()
            active_threads.append(t)
        
        current_threads = initial_threads
        ramp_start = time.time()
        
        # Calculate how often to add threads
        total_threads_to_add = max_threads - initial_threads
        if total_threads_to_add > 0 and ramp_duration > 0:
            steps = total_threads_to_add // ramp_step
            interval = ramp_duration / steps if steps > 0 else 0
            
            while current_threads < max_threads:
                time.sleep(interval)
                
                # Add more threads
                threads_to_add = min(ramp_step, max_threads - current_threads)
                for i in range(threads_to_add):
                    t = threading.Thread(
                        target=worker_thread,
                        args=(image_files, api_url, output_format, stats, stop_event, 0),
                        daemon=True
                    )
                    t.start()
                    active_threads.append(t)
                
                current_threads += threads_to_add
                elapsed = time.time() - ramp_start
                print(f"  {elapsed:.1f}s - Active threads: {current_threads}")
        
        # Phase 2: Sustain max load
        print(f"\nPHASE 2: Sustaining {max_threads} threads for {sustain_duration}s")
        sustain_start = time.time()
        
        while time.time() - sustain_start < sustain_duration:
            remaining = sustain_duration - (time.time() - sustain_start)
            print(f"  {remaining:.0f}s remaining...", end='\r')
            time.sleep(1)
        
        print("\n\nStopping all threads...")
        
    finally:
        # Stop all threads
        stop_event.set()
        stats.end_time = time.time()
        
        # Wait for threads to finish (with timeout)
        for t in active_threads:
            t.join(timeout=2)
    
    # Print final statistics
    print_stats(stats, "FINAL RESULTS")
    
    return stats.get_stats()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark FFmpeg image conversion API")
    parser.add_argument("image_dir", help="Directory containing AVIF images")
    parser.add_argument("--api-url",
                       help="API endpoint URL")
    parser.add_argument("--format", default="jpg", choices=["jpg", "png", "webp", "avif"],
                       help="Output format")
    parser.add_argument("--initial-threads", type=int, default=1,
                       help="Initial number of concurrent threads")
    parser.add_argument("--max-threads", type=int, default=10,
                       help="Maximum number of concurrent threads")
    parser.add_argument("--ramp-duration", type=int, default=30,
                       help="Ramp-up duration in seconds")
    parser.add_argument("--sustain-duration", type=int, default=60,
                       help="Sustain duration in seconds")
    parser.add_argument("--ramp-step", type=int, default=1,
                       help="Number of threads to add at each ramp step")
    
    args = parser.parse_args()
    
    run_benchmark(
        image_dir=args.image_dir,
        api_url=args.api_url,
        output_format=args.format,
        initial_threads=args.initial_threads,
        max_threads=args.max_threads,
        ramp_duration=args.ramp_duration,
        sustain_duration=args.sustain_duration,
        ramp_step=args.ramp_step
    )