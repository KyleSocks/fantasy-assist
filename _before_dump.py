import json
from src.api.routes import build_board

board = build_board('kyle_12_points')
rows = []
for p in board['players']:
    rows.append({'rank': p['rank'], 'id': p['id'], 'name': p['name'],
                 'pos': p['pos'], 'team': p['team'],
                 'vorp': p['vorp']})
out = {'league': board['league'], 'season': board['season'],
       'count': board['count'], 'players': rows}
with open('before_board.json', 'w') as f:
    json.dump(out, f, indent=1)
print('captured', len(rows), 'players; season', board['season'])
for p in board['players'][:20]:
    print(p['rank'], p['name'], p['pos'], p['vorp'])
