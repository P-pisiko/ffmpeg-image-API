import subprocess
import threading
import queue
import uuid
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

job_queue = queue.Queue()


class Job:
    def __init__(self, image_bytes, out_format):
        self.id = str(uuid.uuid4())
        self.image_bytes = image_bytes
        self.out_format = out_format
        self.result_queue = queue.Queue(maxsize=1)


def ffmpeg_convert(input_bytes, out_format):
    codec_map = {
        "jpg": "mjpeg",
        "jpeg": "mjpeg",
        "png": "png",
        "webp": "libwebp",
        "avif": "libaom-av1"
    }

    if out_format not in codec_map:
        raise RuntimeError("Unsupported format")
    
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",            # Read input from stdin
        "-f", "image2pipe",
        "-vcodec", codec_map[out_format],# Output codec/format
        "pipe:1"                   # Write output to stdout
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    out, err = process.communicate(input=input_bytes)

    if process.returncode != 0:
        raise RuntimeError(err.decode())

    return out


def worker():
    while True:
        job = job_queue.get()
        try:
            result = ffmpeg_convert(job.image_bytes, job.out_format)
            job.result_queue.put(("ok", result))
        except Exception as e:
            job.result_queue.put(("error", str(e)))
        finally:
            job_queue.task_done()


# Start worker thread
threading.Thread(target=worker, daemon=True).start()


@app.route("/convert/", methods=["POST"])
def convert():
    if "file" not in request.files:
        return jsonify({"error": "file field missing"}), 400

    out_format = request.form.get("format", "jpg")

    # Basic validatio
    if out_format not in ["webp", "png", "jpg", "avif"]:
        return jsonify({"error": "unsupported format"}), 400

    image_bytes = request.files["file"].read()

    if len(image_bytes) > 20 * 1024 * 1024:
        return jsonify({"error": "file too large"}), 400

    job = Job(image_bytes, out_format)
    job_queue.put(job)

    # Wait for worker result
    status, payload = job.result_queue.get()

    if status == "error":
        return jsonify({"error": payload}), 500

    return Response(payload, mimetype=f"image/{out_format}")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
