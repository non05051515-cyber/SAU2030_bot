import unittest
import bot
class FakeAPI:
 def __init__(self): self.calls=[]
 def call(self,method,**kw): self.calls.append((method,kw));return True
 def photo(self,*args): self.calls.append(('photo',args))
class Tests(unittest.TestCase):
 def test_all_callback_routes(self):
  todo=['home']; seen=set()
  while todo:
   action=todo.pop()
   if action in seen:continue
   seen.add(action); text,rows,photo=bot.screen(action)
   self.assertTrue(text)
   if photo:self.assertTrue(photo.exists());self.assertLessEqual(len(text),1024)
   for row in rows:
    for b in row:
     self.assertLessEqual(len(b['callback_data'].encode()),64)
     todo.append(b['callback_data'])
  self.assertEqual(len(seen),25)
 def test_private_callback_ack(self):
  api=FakeAPI();bot.handle(api,{'callback_query':{'id':'q','data':'order:youtube','message':{'chat':{'id':1,'type':'private'}}}})
  self.assertEqual(api.calls[0][0],'answerCallbackQuery')
  self.assertIn('لم يتم تسجيل',api.calls[1][1]['text'])
 def test_group_ignored(self):
  api=FakeAPI();bot.handle(api,{'message':{'chat':{'id':2,'type':'group'},'text':'/start'}})
  self.assertEqual(api.calls,[])
 def test_unknown_button(self):
  self.assertEqual(bot.screen('product:missing'),bot.screen('home'))
if __name__=='__main__':unittest.main()
