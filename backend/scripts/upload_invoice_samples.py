from pathlib import Path
import json
import os
import sys

from dotenv import dotenv_values, load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
values = dotenv_values(ENV_PATH)
load_dotenv(ENV_PATH)
if values.get('ALLOWED_ORIGINS'):
    os.environ['ALLOWED_ORIGINS'] = json.dumps([origin.strip() for origin in values['ALLOWED_ORIGINS'].split(',') if origin.strip()])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.supabase_storage import SupabaseStorageClient  # noqa: E402

SAMPLES_DIR = Path(__file__).resolve().parents[1] / 'generated_samples'


def main() -> None:
    client = SupabaseStorageClient(Settings(_env_file=None))
    if not client.configured:
        raise RuntimeError('Supabase Storage credentials are not configured or SUPABASE_URL is invalid')
    pdfs = sorted(SAMPLES_DIR.glob('*.pdf'))
    if len(pdfs) != 5:
        raise RuntimeError(f'Expected exactly five generated PDFs, found {len(pdfs)}')
    for pdf in pdfs:
        key = f'samples/{pdf.name}'
        client.upload_file(key, pdf.read_bytes(), 'application/pdf')
        print(key)


if __name__ == '__main__':
    main()
