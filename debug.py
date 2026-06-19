import json, glob, os

files = sorted(glob.glob('outputs/*.json'), key=os.path.getmtime, reverse=True)
if not files:
    print('No output files found in outputs/')
else:
    print('Reading:', files[0])
    with open(files[0]) as f:
        data = json.load(f)

    pm  = data.get('pm_decision', {})
    rv  = data.get('research_verdict', {})
    tr  = data.get('trader_order', {})
    rsk = data.get('risk_validation', {})

    print('\n=== RESEARCH MANAGER ===')
    print('Recommendation:', rv.get('recommendation'))
    print('Confidence:    ', rv.get('confidence'))
    print('Stop loss:     ', rv.get('stop_loss'))
    print('Target 1:      ', rv.get('target1'))
    print('R:R:           ', rv.get('risk_reward'))
    print('Decision:      ', rv.get('decision'))

    print('\n=== TRADER ===')
    print('Symbol:   ', tr.get('symbol'))
    print('Action:   ', tr.get('transaction_type'))
    print('Qty:      ', tr.get('quantity'))
    print('Price:    ', tr.get('price'))
    print('Stop:     ', tr.get('stop_loss'))
    print('Target:   ', tr.get('take_profit'))
    print('Execute:  ', tr.get('execute'))

    print('\n=== RISK MANAGER ===')
    print('Approve:  ', rsk.get('approve'))
    print('Rating:   ', rsk.get('overall_risk_rating'))
    print('Comments: ', rsk.get('risk_comments'))

    print('\n=== PORTFOLIO MANAGER ===')
    print('Decision:       ', pm.get('decision'))
    print('Criteria passed:', pm.get('criteria_passed'))
    print('Criteria failed:', pm.get('criteria_failed'))
    print('PM note:        ', pm.get('pm_note'))
    print('Final order:    ', pm.get('final_order'))