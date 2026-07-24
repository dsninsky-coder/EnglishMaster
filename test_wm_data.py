import sys, io
sys.path.insert(0, r'F:/项目开发/英语大师/backend')
from app import app

with app.app_context():
    from word_data import WordDataManager
    wdm = WordDataManager()
    u = 'wmtest'

    # 1) words
    wdm.save_words({'测试清单': [{'word': 'apple', 'meaning': '苹果'},
                                 {'word': 'banana', 'meaning': '香蕉'}]})
    print('lists:', wdm.get_list_names(), '| count:', wdm.get_list_word_count())

    # 2) coins + ledger
    b0 = wdm.get_coins_balance(u)
    wdm.add_coins(u, 5, '测试收入')
    wdm.add_coins(u, -2, '测试支出')
    b1 = wdm.get_coins_balance(u)
    ledger = wdm.get_coins_ledger(u, limit=10)
    print('coins balance:', b0, '->', b1, '| ledger entries:', len(ledger))
    assert b1 == b0 + 3, 'ledger balance mismatch'
    assert ledger and 'balance' in ledger[0], 'ledger shape wrong'

    # 3) products (builtin ticket seeded)
    prods = wdm.get_products(active_only=False)
    print('products:', [(p['id'], p['name'], p['type'], p['active']) for p in prods])
    assert any(p['type'] == 'builtin' for p in prods), 'builtin ticket missing'
    pid = wdm.add_product('测试商品', 'desc', 9)
    wdm.toggle_product(pid, False)
    prods2 = wdm.get_products(active_only=True)
    assert not any(p['id'] == pid for p in prods2), 'toggle failed'
    wdm.delete_product(pid)
    print('add/toggle/delete product OK')

    # 4) ticket
    print('ticket_active before:', wdm.is_ticket_active())
    wdm.set_ticket_active(True)
    print('ticket_active after set True:', wdm.is_ticket_active())
    wdm.set_ticket_active(False)

    # 5) wishes (shared Wish table, source='word')
    wid = wdm.create_wish(u, '测试心愿', '想要一本词典', 3, True)
    wdm.pledge_wish(wid, u, 2)
    ws = wdm.get_wishes(requester=u, is_admin=False)
    w = next(x for x in ws if x['id'] == wid)
    print('wish:', w['id'], w['title'], 'pledged=', w['pledged_coins'], 'status=', w['status'], 'source=', w.get('source'))
    assert w['pledged_coins'] == 5, 'pledged should be self(3)+pledge(2)=5'
    assert w['source'] == 'word'
    wdm.update_wish_status(wid, 'approved')
    w2 = wdm.get_wish_by_id(wid)
    print('after approve status=', w2['status'], 'lit=', w2['lit'])
    assert w2['status'] == 'approved' and w2['lit'] is True

    # 6) orders
    oid = wdm.create_order(u, prods[0]['id'], prods[0]['name'], prods[0]['price'])
    ords = wdm.get_orders(username=u)
    print('orders for user:', len(ords))
    wdm.update_order_status(oid, 'completed')

    # 7) receipt checkin grant
    r = wdm.try_grant_checkin(u, 'a', 1, '单元测试')
    print('checkin grant:', r)

    # ---- cleanup so dev DB stays clean ----
    from models import db, Wish, PurchaseOrder, WordList, Word
    Wish.query.filter_by(id=wid).delete()
    PurchaseOrder.query.filter_by(id=oid).delete()
    Word.query.delete(); WordList.query.delete()
    db.session.commit()
    wdm.set_ticket_active(True)  # restore builtin ticket default
    print('\nALL WM DATA-LAYER CHECKS PASSED (and dev DB cleaned)')
