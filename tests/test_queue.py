import os, sys, tempfile, unittest, pathlib, sqlite3, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory()
os.environ['SOCIALPLAN_DB']=str(pathlib.Path(TMP.name)/'test.sqlite3')
import app
from publishing import attach_publishing
# The publishing router creates the publish_jobs table during import.
class QueueTests(unittest.TestCase):
    def test_blocked_and_queued_and_cancelled(self):
        with app.conn() as c:
            c.execute('DELETE FROM publish_jobs')
            c.execute('DELETE FROM audit_events')
            c.execute('DELETE FROM posts')
            c.execute('DELETE FROM sessions')
            c.execute('DELETE FROM users')
            c.execute('DELETE FROM businesses')
            b=c.execute("INSERT INTO businesses(name) VALUES('Test')").lastrowid
            u=c.execute("INSERT INTO users(business_id,email,salt,passhash,role) VALUES(?,?,?,?,?)",(b,'test@example.com','00','hash','admin')).lastrowid
            for status,offset in [('approved',-120),('approved',120),('cancelled',-120),('draft',-120)]:
                when=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()+offset))
                c.execute('INSERT INTO posts(business_id,author_id,caption,media_url,scheduled_at,status,created_at) VALUES(?,?,?,?,?,?,?)',(b,u,'Test','https://example.com/a.jpg',when,status,int(time.time())))
        app.sync_queue()
        with app.conn() as c:
            rows=c.execute('SELECT state FROM publish_jobs ORDER BY post_id').fetchall()
        self.assertEqual([r['state'] for r in rows],['blocked','queued','cancelled'])
if __name__=='__main__':unittest.main()
