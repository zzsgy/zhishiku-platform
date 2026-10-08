"""Single-machine persistent queue. A worker process owns a renewable lease."""
import json
import time
import uuid
from datetime import timedelta
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import Job


def enqueue(kind, payload, key=None):
    try:
        with transaction.atomic():
            return Job.objects.create(kind=kind, payload=payload, active_key=key)
    except IntegrityError:
        return Job.objects.get(active_key=key)


def recover_expired():
    expired = Job.objects.filter(status='running', lease_until__lt=timezone.now())
    for job in expired:
        if Job.objects.filter(pk=job.pk, status='running', lease_until__lt=timezone.now()).update(
            status='failed', error='工作进程中断或租约过期；已停止，确认原件后可重试', active_key=None):
            if job.kind == 'backup':
                from systemsettings.models import BackupJob
                BackupJob.objects.filter(pk=job.payload['backup_id']).update(status='failed',
                    message='工作进程中断，请重新发起备份', finished=timezone.now())
            elif job.kind == 'video':
                from .models import CollectionItem
                CollectionItem.objects.filter(pk=job.payload['item_id']).update(status='failed', note='工作进程中断，请检查原件后重试')
    from systemsettings.models import BackupJob
    active_ids = [row['backup_id'] for row in Job.objects.filter(kind='backup', status__in=['pending', 'running']).values_list('payload', flat=True)]
    BackupJob.objects.filter(status__in=['pending', 'running'], created__lt=timezone.now()-timedelta(minutes=2)).exclude(pk__in=active_ids).update(
        status='failed', message='旧任务缺少执行记录，已解除阻塞，请重新备份', finished=timezone.now())


def run_one():
    recover_expired()
    job = Job.objects.filter(status='pending').order_by('created', 'pk').first()
    if not job:
        return False
    owner = uuid.uuid4().hex
    if not Job.objects.filter(pk=job.pk, status='pending').update(status='running', owner=owner,
            lease_until=timezone.now() + timedelta(minutes=10), attempts=job.attempts + 1):
        return True
    last_heartbeat = 0
    def heartbeat(*_):
        nonlocal last_heartbeat
        if time.monotonic() - last_heartbeat > 5:
            last_heartbeat = time.monotonic()
            if not Job.objects.filter(pk=job.pk, owner=owner, status='running').update(
                lease_until=timezone.now() + timedelta(minutes=10)):
                raise RuntimeError('任务租约丢失，已停止发布')
    try:
        if job.kind == 'backup':
            from systemsettings.views import _run_backup_job
            _run_backup_job(job.payload['backup_id'], job.payload['kind'], job.payload['options'], heartbeat=heartbeat)
            from systemsettings.models import BackupJob
            backup = BackupJob.objects.get(pk=job.payload['backup_id'])
            if backup.status != 'done':
                raise RuntimeError(backup.message or '备份失败')
            result = {'backup_id': backup.pk}
        elif job.kind == 'video':
            from .models import CollectionItem
            from .media import video_to_markdown
            from .services import link_collection_to_node
            from django.conf import settings
            item = CollectionItem.objects.get(pk=job.payload['item_id'])
            title, md = video_to_markdown(item.source_url, settings.MEDIA_ROOT / 'videos' / owner, heartbeat=heartbeat)
            item.parsed_md = md
            item.save(update_fields=['parsed_md'])
            node = link_collection_to_node(item, base=item.base, title=title, category='视频转图文')
            item.status = 'done' if node.export_status == 'done' else 'failed'
            item.title, item.note = title, '转写/截图已保存；请核对实际内容与原件'
            item.save()
            if node.export_status != 'done':
                raise RuntimeError('视频正文已入库，文件导出失败；请在运行诊断中重试导出')
            result = {'node_id': node.pk}
        else:
            raise ValueError('不支持的任务类型')
        Job.objects.filter(pk=job.pk, owner=owner, status='running').update(status='done', result=result,
            active_key=None, finished=timezone.now())
    except Exception as exc:
        Job.objects.filter(pk=job.pk, owner=owner, status='running').update(status='failed', error=str(exc)[:1000],
            active_key=None, finished=timezone.now())
        if job.kind == 'video':
            from .models import CollectionItem
            CollectionItem.objects.filter(pk=job.payload['item_id']).update(status='failed', note=str(exc)[:1000])
    return True
