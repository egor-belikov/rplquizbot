# server.py (версия 26 - Убран лишний monkey_patch)

import os, csv, uuid, random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from fuzzywuzzy import fuzz
from glicko2 import Player
from sqlalchemy.pool import NullPool

# Константы
TOTAL_ROUNDS = 16
PAUSE_BETWEEN_ROUNDS = 10
TURN_TIME_LIMIT = 15
TYPO_THRESHOLD = 85

# Настройка Flask, SQLAlchemy
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_very_secret_key'
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'game.db')
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = { 'poolclass': NullPool }
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# Gunicorn с --worker-class eventlet сам сделает monkey-patching,
# поэтому SocketIO нужно инициализировать без явного указания async_mode.
# Но для совместимости с локальным запуском оставим eventlet.
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Модель Базы Данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(80), unique=True, nullable=False)
    rating = db.Column(db.Float, default=1500)
    rd = db.Column(db.Float, default=350)
    vol = db.Column(db.Float, default=0.06)

with app.app_context():
    db.create_all()

# Функции для работы с БД
def get_or_create_user(nickname):
    user = User.query.filter_by(nickname=nickname).first()
    if not user:
        user = User(nickname=nickname)
        db.session.add(user)
        db.session.commit()
    return user

def update_ratings(winner_user, loser_user):
    with app.app_context():
        winner_player = Player(rating=winner_user.rating, rd=winner_user.rd, vol=winner_user.vol)
        loser_player = Player(rating=loser_user.rating, rd=loser_user.rd, vol=loser_user.vol)
        winner_player.update_player([loser_player.rating], [loser_player.rd], [1])
        loser_player.update_player([winner_player.rating], [winner_player.rd], [0])
        winner_user.rating, winner_user.rd, winner_user.vol = winner_player.rating, winner_player.rd, winner_player.vol
        loser_user.rating, loser_user.rd, loser_user.vol = loser_player.rating, loser_player.rd, loser_player.vol
        db.session.commit()
        print(f"Рейтинги обновлены: {winner_user.nickname} ({int(winner_user.rating)}), {loser_user.nickname} ({int(loser_user.rating)})")

def load_player_data(filename):
    clubs_data = {}
    with open(filename, mode='r', encoding='utf-8') as infile:
        reader = csv.reader(infile)
        for row in reader:
            if not row or not row[0]: continue
            player_name_full, club_name = row[0], row[1]
            primary_surname = player_name_full.split()[-1]
            aliases = {primary_surname}
            if len(row) > 2:
                for alias in row[2:]:
                    if alias: aliases.add(alias)
            player_object = { 'primary_name': primary_surname, 'valid_normalized_names': {a.strip().lower().replace('ё', 'е') for a in aliases} }
            if club_name not in clubs_data: clubs_data[club_name] = []
            clubs_data[club_name].append(player_object)
    return clubs_data

all_clubs_data = load_player_data('players.csv')

class GameState:
    def __init__(self, player1_info, all_clubs, player2_info=None, mode='solo'):
        self.mode = mode
        self.players = {0: player1_info}
        if player2_info: self.players[1] = player2_info
        self.scores = {0: 0, 1: 0}
        self.game_clubs = random.sample(list(all_clubs_data.keys()), TOTAL_ROUNDS)
        self.all_clubs_data = all_clubs_data
        self.current_round, self.current_player_index, self.current_club_name = -1, 0, None
        self.players_for_comparison, self.named_players_primary, self.named_players = [], set(), []
        self.round_history = []
    def start_new_round(self):
        if self.is_game_over(): return False
        self.current_round += 1
        self.current_club_name = self.game_clubs[self.current_round]
        player_objects = self.all_clubs_data[self.current_club_name]
        self.players_for_comparison = sorted(player_objects, key=lambda p: p['primary_name'])
        self.named_players_primary, self.named_players = set(), []
        self.current_player_index = self.current_round % len(self.players)
        return True
    def process_guess(self, guess):
        guess_norm = guess.strip().lower().replace('ё', 'е')
        for player_data in self.players_for_comparison:
            if player_data['primary_name'] in self.named_players_primary: continue
            if guess_norm in player_data['valid_normalized_names']: return {'result': 'correct', 'player_data': player_data}
        best_match_player, max_ratio = None, 0
        for player_data in self.players_for_comparison:
            if player_data['primary_name'] in self.named_players_primary: continue
            primary_norm = player_data['primary_name'].lower().replace('ё', 'е')
            ratio = fuzz.ratio(guess_norm, primary_norm)
            if ratio > max_ratio: max_ratio, best_match_player = ratio, player_data
        if max_ratio >= TYPO_THRESHOLD: return {'result': 'correct_typo', 'player_data': best_match_player}
        return {'result': 'not_found'}
    def add_named_player(self, player_data, player_index):
        self.named_players.append({'name': player_data['primary_name'], 'by': player_index})
        self.named_players_primary.add(player_data['primary_name'])
        if self.mode != 'solo': self.switch_player()
    def switch_player(self): self.current_player_index = 1 - self.current_player_index
    def is_round_over(self): return len(self.named_players) == len(self.players_for_comparison)
    def is_game_over(self): return self.current_round >= TOTAL_ROUNDS - 1

active_games = {}
lobby_players = {}

def get_game_state_for_client(game, room_id):
    return { 'roomId': room_id, 'mode': game.mode, 'players': {i: {'nickname': p['nickname']} for i, p in game.players.items()}, 'scores': game.scores, 'round': game.current_round + 1, 'totalRounds': TOTAL_ROUNDS, 'clubName': game.current_club_name, 'namedPlayers': game.named_players, 'fullPlayerList': [p['primary_name'] for p in game.players_for_comparison], 'currentPlayerIndex': game.current_player_index, 'timeLimit': TURN_TIME_LIMIT }

def start_next_human_turn(room_id):
    game_session = active_games.get(room_id)
    if not game_session: return
    game = game_session['game']
    turn_id = f"{room_id}_{game.current_round}_{len(game.named_players)}"
    game_session['turn_id'] = turn_id
    socketio.start_background_task(turn_watcher, room_id, turn_id, TURN_TIME_LIMIT)
    socketio.emit('turn_updated', get_game_state_for_client(game, room_id), room=room_id)

def turn_watcher(room_id, turn_id, time_limit):
    socketio.sleep(time_limit)
    game_session = active_games.get(room_id)
    if game_session and game_session.get('turn_id') == turn_id: on_timer_end(room_id)

def on_timer_end(room_id):
    game_session = active_games.get(room_id)
    if not game_session: return
    game = game_session['game']
    socketio.emit('timer_expired', {'playerIndex': game.current_player_index}, room=room_id)
    if game.mode != 'solo':
        game.scores[1 - game.current_player_index] += 1
    show_round_summary_and_schedule_next(room_id)

def start_game_loop(room_id):
    game_session = active_games.get(room_id)
    if not game_session: return
    game = game_session['game']
    if not game.start_new_round():
        game_over_data = { 'final_scores': game.scores, 'players': {i: {'nickname': p['nickname']} for i, p in game.players.items()}, 'history': game.round_history, 'mode': game.mode }
        socketio.emit('game_over', game_over_data, room=room_id)
        if game.mode == 'pvp':
            player1, player2 = game.players[0]['user_obj'], game.players[1]['user_obj']
            if game.scores[0] > game.scores[1]: update_ratings(winner_user=player1, loser_user=player2)
            elif game.scores[1] > game.scores[0]: update_ratings(winner_user=player2, loser_user=player1)
        if room_id in active_games: del active_games[room_id]
        return
    socketio.emit('round_started', get_game_state_for_client(game, room_id), room=room_id)
    start_next_human_turn(room_id)

def show_round_summary_and_schedule_next(room_id):
    game_session = active_games.get(room_id)
    if not game_session: return
    game = game_session['game']
    p1_named_count = len([p for p in game.named_players if p['by'] == 0])
    p2_named_count = len([p for p in game.named_players if p.get('by') == 1])
    round_result = { 'club_name': game.current_club_name, 'p1_named': p1_named_count, 'p2_named': p2_named_count }
    game.round_history.append(round_result)
    summary_data = { 'clubName': game.current_club_name, 'fullPlayerList': [p['primary_name'] for p in game.players_for_comparison], 'namedPlayers': game.named_players, 'players': {i: {'nickname': p['nickname']} for i, p in game.players.items()}, 'scores': game.scores, 'mode': game.mode }
    socketio.emit('round_summary', summary_data, room=room_id)
    pause_id = f"pause_{room_id}_{game.current_round}"
    game_session['pause_id'] = pause_id
    socketio.start_background_task(pause_watcher, room_id, pause_id)

def pause_watcher(room_id, pause_id):
    socketio.sleep(PAUSE_BETWEEN_ROUNDS)
    game_session = active_games.get(room_id)
    if game_session and game_session.get('pause_id') == pause_id:
        start_game_loop(room_id)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in lobby_players:
        nickname = lobby_players[request.sid].get('nickname', 'Unknown')
        del lobby_players[request.sid]
        print(f"Игрок {nickname} покинул лобби.")

@socketio.on('skip_pause')
def handle_skip_pause(data):
    room_id = data.get('roomId')
    game_session = active_games.get(room_id)
    if game_session:
        game_session['pause_id'] = None
        start_game_loop(room_id)

@socketio.on('cancel_pvp_search')
def handle_cancel_pvp_search():
    sid = request.sid
    if sid in lobby_players:
        nickname = lobby_players.get(sid, {}).get('nickname', 'Unknown')
        del lobby_players[sid]
        print(f"Игрок {nickname} отменил поиск.")

@socketio.on('get_leaderboard')
def handle_get_leaderboard():
    with app.app_context():
        users = User.query.filter(User.nickname != 'Робо-Квинси').order_by(User.rating.desc()).all()
        leaderboard_data = [{'nickname': user.nickname, 'rating': int(user.rating)} for user in users]
        emit('leaderboard_data', leaderboard_data)

@socketio.on('register_user')
def handle_register_user(data):
    nickname = data.get('nickname')
    if not nickname or len(nickname) < 3:
        emit('registration_status', {'success': False, 'message': 'Никнейм должен быть длиннее 2 символов.'})
        return
    with app.app_context():
        user_exists = User.query.filter_by(nickname=nickname).first()
        if user_exists:
            emit('registration_status', {'success': False, 'message': 'Этот никнейм уже занят.'})
        else:
            get_or_create_user(nickname)
            emit('registration_status', {'success': True, 'nickname': nickname})

@socketio.on('start_game')
def handle_start_game(data):
    sid, mode, nickname = request.sid, data.get('mode'), data.get('nickname')
    player_info = {'sid': sid, 'nickname': nickname} 
    if mode == 'solo' or mode == 'vs_bot':
        with app.app_context():
            player_user = get_or_create_user(nickname)
        player1_info_full = {'sid': sid, 'nickname': nickname, 'user_obj': player_user}
        room_id = str(uuid.uuid4())
        join_room(room_id)
        player2_info = None
        if mode == 'vs_bot':
            with app.app_context():
                bot_user = get_or_create_user('Робо-Квинси')
            player2_info = {'sid': 'BOT', 'nickname': 'Робо-Квинси', 'user_obj': bot_user}
        game = GameState(player1_info_full, all_clubs_data, player2_info=player2_info, mode=mode)
        active_games[room_id] = {'game': game, 'turn_id': None, 'pause_id': None}
        start_game_loop(room_id)
    elif mode == 'pvp':
        if sid in lobby_players: return
        lobby_players[sid] = player_info
        print(f"Игрок {nickname} вошел в лобби. Всего: {len(lobby_players)}")
        if len(lobby_players) >= 2:
            p1_sid, p1_info = list(lobby_players.items())[0]
            p2_sid, p2_info = list(lobby_players.items())[1]
            del lobby_players[p1_sid]
            del lobby_players[p2_sid]
            with app.app_context():
                p1_user = get_or_create_user(p1_info['nickname'])
                p2_user = get_or_create_user(p2_info['nickname'])
            p1_info_full = {'sid': p1_sid, 'nickname': p1_info['nickname'], 'user_obj': p1_user}
            p2_info_full = {'sid': p2_sid, 'nickname': p2_info['nickname'], 'user_obj': p2_user}
            room_id = str(uuid.uuid4())
            join_room(room_id, sid=p1_sid)
            join_room(room_id, sid=p2_sid)
            game = GameState(p1_info_full, all_clubs_data, player2_info=p2_info_full, mode='pvp')
            active_games[room_id] = {'game': game, 'turn_id': None, 'pause_id': None}
            print(f"Начинается PvP игра: {p1_info['nickname']} vs {p2_info['nickname']}")
            start_game_loop(room_id)
        else:
            emit('waiting_for_opponent')

@socketio.on('submit_guess')
def handle_submit_guess(data):
    room_id, guess = data.get('roomId'), data.get('guess')
    game_session = active_games.get(room_id)
    if not game_session: return
    game_session['turn_id'] = None
    game = game_session['game']
    result = game.process_guess(guess)
    if result['result'] in ['correct', 'correct_typo']:
        player_data = result['player_data']
        current_player_index = game.current_player_index
        game.add_named_player(player_data, current_player_index)
        if game.mode == 'pvp':
            game.scores[current_player_index] += 1
        emit('guess_result', {'result': result['result'], 'corrected_name': player_data['primary_name']})
        if game.is_round_over():
            show_round_summary_and_schedule_next(room_id)
        else:
            if game.mode == 'solo': start_next_human_turn(room_id)
            elif game.mode == 'vs_bot' and game.current_player_index == 1: socketio.start_background_task(bot_turn, room_id)
            elif game.mode == 'pvp': start_next_human_turn(room_id)
    else:
        emit('guess_result', {'result': result['result']})

@socketio.on('surrender_round')
def handle_surrender(data):
    room_id = data.get('roomId')
    game_session = active_games.get(room_id)
    if not game_session: return
    game_session['turn_id'] = None 
    on_timer_end(room_id)

def bot_turn(room_id):
    game_session = active_games.get(room_id)
    if not game_session: return
    game = game_session['game']
    socketio.sleep(0.5)
    remaining_players = [p for p in game.players_for_comparison if p['primary_name'] not in game.named_players_primary]
    if remaining_players:
        bot_choice = random.choice(remaining_players)
        game.add_named_player(bot_choice, 1)
        if game.mode == 'vs_bot': game.scores[1] += 1
        socketio.emit('bot_guessed', {'guess': bot_choice['primary_name']}, room=room_id)
    if not game.is_round_over():
        start_next_human_turn(room_id)
    else:
        show_round_summary_and_schedule_next(room_id)

@app.route('/')
def index(): return render_template('index.html')

if __name__ == '__main__':
    # При локальном запуске monkey_patch нужен
    import eventlet
    eventlet.monkey_patch()
    if not all_clubs_data: print("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить players.csv")
    else:
        print("Сервер запускается...")
        socketio.run(app, host='127.0.0.1', port=5000, debug=True)