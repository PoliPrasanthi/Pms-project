import sys
from fastapi.testclient import TestClient
sys.path.insert(0, ".")
from app.main import app

client = TestClient(app)
urls = [
    '/api/v1/projects/export/csv',
    '/api/v1/tasks/export/csv',
    '/api/v1/issues/export/csv',
    '/api/v1/timelogs/export/csv',
    '/api/v1/reports/export/csv?report_type=projects',
    '/api/v1/reports/export/csv?report_type=tasks',
    '/api/v1/reports/export/csv?report_type=issues',
    '/api/v1/reports/export/csv?report_type=timelogs'
]
for u in urls:
    print('Testing', u)
    try:
        r = client.get(u)
        print('Status:', r.status_code)
        if r.status_code != 200:
            print(r.text)
        else:
            print('Lines:', len(r.text.split('\n')))
            if len(r.text.split('\n')) > 1:
                print(r.text.split('\n')[1][:100])
    except Exception as e:
        import traceback
        traceback.print_exc()
