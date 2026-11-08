import http.client,json,threading,time,unittest
import test_gateway as fixtures
gateway=fixtures.gateway
class HttpControl(unittest.TestCase):
 def setUp(self):
  helper=fixtures.GatewayTests();helper.setUp();self.app=helper.app
  self.server=gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
  self.server.daemon_threads=True;self.server.app=self.app
  self.app.args.bind='127.0.0.1';self.app.args.port=self.server.server_port
  self.origin='http://127.0.0.1:'+str(self.server.server_port)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.cookie=self.call('GET','/api/session')[2]['Set-Cookie'].split(';')[0]
 def tearDown(self):
  self.app.close();self.server.shutdown();self.server.server_close();self.thread.join()
 def call(self,method,path,data=None,cookie=None):
  con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
  headers={'Origin':self.origin,'Cookie':cookie if cookie is not None else getattr(self,'cookie','')}
  if data is not None:headers['Content-Type']='application/json'
  con.request(method,path,json.dumps(data) if data is not None else None,headers)
  r=con.getresponse();body=r.read();result=(r.status,json.loads(body),dict(r.getheaders()));con.close();return result
 def test_old_cached_ui_cannot_drive(self):
  code,body,_=self.call('POST','/api/arm',{})
  self.assertEqual(code,426);self.assertIsNone(self.app.owner)
  self.assertEqual(self.call('POST','/api/arm',{'protocol':3})[0],426)
 def test_out_of_order_move_cannot_override_neutral(self):
  code,body,_=self.call('POST','/api/arm',{'protocol':4});self.assertEqual(code,200);lease=body['lease']
  self.assertEqual(self.call('POST','/api/drive',{'lease':lease,'seq':2,'vx':0,'max_speed':100})[0],200)
  code,body,_=self.call('POST','/api/drive',{'lease':lease,'seq':1,'vx':.35,'max_speed':100})
  self.assertEqual(code,409);self.assertEqual(body['code'],'stale_command');self.assertTrue(all(not any(c) for c in self.app.motors.commands))
  self.assertEqual(self.call('POST','/api/release',{'lease':lease})[0],200)
  self.assertIsNone(self.app.owner);self.assertEqual(self.app.motors.commands[-1],(0,0,0))
 def test_spectator_page_cannot_release_driver(self):
  lease=self.call('POST','/api/arm',{'protocol':4})[1]['lease']
  spectator=self.call('GET','/api/session',cookie='')[2]['Set-Cookie'].split(';')[0]
  self.call('POST','/api/release',{'lease':lease},cookie=spectator)
  self.assertIsNotNone(self.app.owner)
if __name__=='__main__':unittest.main()
