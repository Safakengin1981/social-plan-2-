import os,sys,tempfile,pathlib,unittest
from datetime import datetime,timedelta,timezone
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory()
os.environ['SOCIALPLAN_DB']=str(pathlib.Path(TMP.name)/'api.sqlite3')
import app
from fastapi.testclient import TestClient
class ApiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.client=TestClient(app.app)
 def register_login(self,n):
  email=f'owner{n}@example.test'; password='correct horse battery staple'
  r=self.client.post('/api/register',json={'business':f'Business {n}','email':email,'password':password});self.assertEqual(r.status_code,200,r.text)
  r=self.client.post('/api/login',json={'email':email,'password':password});self.assertEqual(r.status_code,200,r.text)
  return {'Authorization':'Bearer '+r.json()['token']}
 def test_tenant_isolation_and_roles(self):
  a=self.register_login('a'); b=self.register_login('b')
  when=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
  r=self.client.post('/api/posts',headers=a,json={'caption':'RHM','media_url':'https://example.test/p.jpg','scheduled_at':when});self.assertEqual(r.status_code,200,r.text)
  post_id=r.json()['id']
  self.assertEqual(len(self.client.get('/api/posts',headers=b).json()),0)
  self.assertEqual(self.client.post(f'/api/posts/{post_id}/approve',headers=b).status_code,404)
  self.assertEqual(self.client.post(f'/api/posts/{post_id}/approve',headers=a).status_code,200)
  self.assertEqual(len(self.client.get('/api/audit',headers=b).json()),0)
  self.assertNotIn('RHM',self.client.get('/api/posts/export.csv',headers=b).text)
  self.assertIn('RHM',self.client.get('/api/posts/export.csv',headers=a).text)
 def test_export_formula_safety(self):
  a=self.register_login('csv')
  when=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
  r=self.client.post('/api/posts',headers=a,json={'caption':'=HYPERLINK("x")','media_url':'https://example.test/p.jpg','scheduled_at':when});self.assertEqual(r.status_code,200,r.text)
  self.assertIn("'=HYPERLINK",self.client.get('/api/posts/export.csv',headers=a).text)
 def test_login_lockout(self):
  email='lock@example.test'; password='correct horse battery staple'
  self.assertEqual(self.client.post('/api/register',json={'business':'Locked Business','email':email,'password':password}).status_code,200)
  for _ in range(5):
   self.assertEqual(self.client.post('/api/login',json={'email':email,'password':'wrong'}).status_code,401)
  self.assertEqual(self.client.post('/api/login',json={'email':email,'password':password}).status_code,429)
 def test_health(self):
  self.assertEqual(self.client.get('/api/health').json()['instagram_publishing'],'not_configured')
if __name__=='__main__':unittest.main()
