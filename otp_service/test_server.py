import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from otp_service import server as s


class OTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=patch.object(s,'DB',Path(self.temp.name)/'otp.sqlite3');self.path.start()
        s.prepare()
        self.token=s.issue({'order_id':'order1','customer_id':123,'email':'test@example.com','expires':int(time.time())+3600,'limit':2})
        self.send=patch.object(s,'send',return_value={'message_id':1});self.sender=self.send.start()
        self.config=patch.object(s.email_codes,'mailbox_configs',return_value=[{'configured':True}]);self.config.start()

    def tearDown(self):
        self.config.stop();self.send.stop();self.path.stop();self.temp.cleanup()

    def used(self):
        return s.ticket(123,self.token)['used']

    def test_bound_link_and_idempotent_issue(self):
        self.assertIsNone(s.ticket(124,self.token))
        s.request_code(124,self.token)
        self.assertEqual(s.ticket(123,self.token)['status'],'idle')
        with self.assertRaises(ValueError):
            s.issue({'order_id':'order1','customer_id':124,'email':'other@example.com','expires':int(time.time())+3600})
        again=s.issue({'order_id':'order1','customer_id':123,'email':'test@example.com','expires':int(time.time())+7200})
        self.assertEqual(again,self.token)

    def test_two_codes_only_and_no_reuse(self):
        for code,key in [('123456','m1'),('654321','m2')]:
            s.request_code(123,self.token)
            with patch.object(s.email_codes,'fetch_code',return_value=(code,key)):s.tick()
        self.assertEqual(self.used(),2)
        s.request_code(123,self.token)
        self.assertEqual(s.ticket(123,self.token)['status'],'idle')
        self.assertEqual(sum('Login code:' in x.args[1] for x in self.sender.call_args_list),2)
        self.assertEqual(s.issue({'order_id':'order1','customer_id':123,'email':'test@example.com','expires':int(time.time())+3600}),self.token)
        self.assertEqual(self.used(),2)

    def test_timeout_does_not_count(self):
        s.request_code(123,self.token)
        with s.db() as c:c.execute('UPDATE tickets SET requested=?',(int(time.time())-181,))
        s.tick()
        self.assertEqual(self.used(),0)
        self.assertEqual(s.ticket(123,self.token)['status'],'idle')

    def test_consumed_message_cannot_be_delivered_again(self):
        s.request_code(123,self.token)
        with patch.object(s.email_codes,'fetch_code',return_value=('123456','same')):s.tick()
        s.request_code(123,self.token)
        with patch.object(s.email_codes,'fetch_code',return_value=('123456','same')):s.tick()
        self.assertEqual(self.used(),1)

    def test_failed_send_never_retries_or_leaks_code(self):
        s.request_code(123,self.token)
        with patch.object(s.email_codes,'fetch_code',return_value=('123456','m1')),patch.object(s,'send',side_effect=TimeoutError):s.tick()
        row=s.ticket(123,self.token)
        self.assertEqual(row['status'],'uncertain')
        self.assertEqual(row['code'],'')
        self.assertEqual(row['used'],1)
        with patch.object(s.email_codes,'fetch_code') as fetch:s.tick();fetch.assert_not_called()

    def test_missing_mailbox_does_not_start_request(self):
        with patch.object(s.email_codes,'mailbox_configs',return_value=[]):s.request_code(123,self.token)
        self.assertEqual(s.ticket(123,self.token)['status'],'idle')
        self.assertEqual(self.used(),0)


if __name__=='__main__':unittest.main()
