"""Runs in a killable subprocess so speech processing has a firm deadline."""
import json
import sys
import whisper

if __name__ == '__main__':
    model = whisper.load_model('base')
    result = model.transcribe(sys.argv[1])
    print(json.dumps({'text': result['text']}, ensure_ascii=True))
