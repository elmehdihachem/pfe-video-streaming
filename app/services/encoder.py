import ffmpeg
import os
import uuid


def encode_video(input_path, output_folder):
    video_folder_name = str(uuid.uuid4())

    # ✅ Utilise /tmp qui est toujours accessible dans le container
    tmp_dir = f"/tmp/{video_folder_name}"
    os.makedirs(tmp_dir, exist_ok=True)

    playlist_path = os.path.join(tmp_dir, "video.m3u8")
    segment_path = os.path.join(tmp_dir, "chunk_%03d.ts")

    try:

     (
        ffmpeg
        .input(input_path)
        .output(
            playlist_path,
            format="hls",
            hls_time=3,
            hls_list_size=0,
            hls_segment_filename=segment_path,
            vf="scale=1280:720",
            video_bitrate="2500k"
        )
        .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
     )

    except ffmpeg.Error as e:
      #Affiche le vrai message d'erreur ffmpeg
      stderr_output = e.stderr.decode("utf-8") if e.stderr else "Pas de stderr"
      raise Exception(f"FFmpeg error:\n{stderr_output}")

    return playlist_path,video_folder_name