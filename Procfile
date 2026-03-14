web: python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" && gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300
