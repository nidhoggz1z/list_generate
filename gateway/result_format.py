"""The same projection runs in the Dify code node and the gateway."""
import re
from datetime import datetime, timedelta, timezone


CALLBACK_ZONE = timezone(timedelta(hours=8))
CALLBACK_ITEM_FIELDS = ('materialName', 'sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment')


def callback_time(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CALLBACK_ZONE)
    return parsed.astimezone(CALLBACK_ZONE)


def _callback_iso(value):
    return value.replace(tzinfo=None).isoformat(timespec='seconds')


def _callback_publish_date(value, completed):
    text = value.strip().rstrip('。.;； ') if isinstance(value, str) else ''
    try:
        return _callback_iso(callback_time(text))
    except ValueError:
        pass
    normalized = re.sub(r'\s+', '', text).replace('年', '-').replace('月', '-').replace('日', '')
    normalized = normalized.replace('/', '-').replace('.', '-').rstrip('-')
    match = re.fullmatch(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', normalized)
    if match:
        year, month, day = match.groups()
        try:
            return _callback_iso(datetime(int(year), int(month or 1), int(day or 1)))
        except ValueError:
            pass
    return _callback_iso(completed.replace(hour=0, minute=0, second=0, microsecond=0))


def build_callback(task_id, status, completed_at, materials=(), error_msg=''):
    completed = callback_time(completed_at)
    if status not in ('DONE', 'FAILED'):
        raise ValueError('无效的回调终态')
    items = []
    if status == 'DONE':
        for material in materials:
            item = {key: material[key] for key in CALLBACK_ITEM_FIELDS}
            item['publishDate'] = _callback_publish_date(item['publishDate'], completed)
            items.append(item)
    return dict(taskId=task_id, taskStatus=status, generatedAt=_callback_iso(completed),
                errorMsg=error_msg, checkListItems=items)
