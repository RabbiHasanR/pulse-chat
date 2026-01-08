from celery import Celery
from kombu import Queue, Exchange
import os


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('core')
app.config_from_object('django.conf:settings', namespace='CELERY')

app.conf.task_queues = (
    Queue('default', Exchange('default'), routing_key='default'),
    Queue('video_queue', Exchange('media'), routing_key='video'),
    Queue('image_queue', Exchange('media'), routing_key='image'),
    Queue('audio_queue', Exchange('media'), routing_key='audio'),
    Queue('file_queue',  Exchange('media'), routing_key='file'),
)

app.conf.task_routes = {
    'background_worker.chats.tasks.notify_message_event': {'queue': 'default'},
    
    'background_worker.chats.tasks.process_video_task': {'queue': 'video_queue'},
    'background_worker.chats.tasks.process_image_task': {'queue': 'image_queue'},
    'background_worker.chats.tasks.process_audio_task': {'queue': 'audio_queue'},
    'background_worker.chats.tasks.process_file_task':  {'queue': 'file_queue'},
}

app.autodiscover_tasks()
