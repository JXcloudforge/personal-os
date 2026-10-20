"""Create a blank persisted message for the isolated browser clipboard test only."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

parser = argparse.ArgumentParser()
parser.add_argument('--conversation-id', required=True)
parser.add_argument('--database', required=True)
args = parser.parse_args()

if os.environ.get('PERSONAL_OS_TESTING') != '1':
    raise SystemExit('Refusing test fixture write without PERSONAL_OS_TESTING=1')

from app.database import SessionLocal, engine
from app.models import ChatConversation, ChatMessage

target = Path(engine.url.database).resolve()
requested = Path(args.database).resolve()
if target != requested or 'personal_os_r033_r0_e2e_' not in str(target).lower():
    raise SystemExit('Refusing fixture write outside the named isolated R0 database')

with SessionLocal() as db:
    conversation = db.get(ChatConversation, args.conversation_id)
    if conversation is None:
        raise SystemExit('Conversation not found in isolated R0 database')
    message = ChatMessage(user_id=conversation.user_id, conversation_id=conversation.id, role='assistant', content='', status='completed')
    db.add(message)
    db.commit()
    db.refresh(message)
    print(message.id)
