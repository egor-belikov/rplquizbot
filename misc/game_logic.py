# game_logic.py (версия 2)

import csv
import random
import os
import time
import threading
import sys

# --- 1. Конфигурация и константы ---
PLAYERS_FILENAME = 'players.csv'
TOTAL_ROUNDS = 16
TIME_FIRST_HALF = 20
TIME_SECOND_HALF = 30
# Новая константа для паузы между раундами
PAUSE_BETWEEN_ROUNDS = 10

# --- 2. Загрузка и подготовка данных ---
# (Эта функция остается без изменений)
def load_player_data(filename):
    if not os.path.exists(filename):
        print(f"Ошибка: Файл '{filename}' не найден. Убедитесь, что он находится в той же папке, что и скрипт.")
        return None
    clubs_data = {}
    with open(filename, mode='r', encoding='utf-8') as infile:
        reader = csv.reader(infile)
        for row in reader:
            if len(row) >= 2:
                player_full_name, club_name = row[0], row[1]
                surname = player_full_name.split()[-1]
                if club_name not in clubs_data:
                    clubs_data[club_name] = []
                clubs_data[club_name].append(surname)
    return clubs_data

# --- НОВИНКА: Функция для ввода с обратным отсчетом ---
# Эта функция использует потоки (threading), чтобы одновременно
# ждать ввод от пользователя и показывать тикающий таймер.
def get_input_with_countdown(prompt, timeout):
    """
    Запрашивает ввод у пользователя с видимым обратным отсчетом.

    Args:
        prompt (str): Сообщение для пользователя.
        timeout (int): Время в секундах.

    Returns:
        str: Ввод пользователя или None, если время вышло.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    
    # Внутренняя функция, которая будет выполняться в отдельном потоке
    result = [None]
    def get_input_target():
        result[0] = sys.stdin.readline().strip()

    input_thread = threading.Thread(target=get_input_target)
    input_thread.daemon = True
    input_thread.start()

    # Главный поток будет показывать таймер
    for i in range(timeout, 0, -1):
        # \r - это специальный символ "возврат каретки", он перемещает курсор в начало строки
        sys.stdout.write(f" Осталось: {i:02} сек \r")
        sys.stdout.flush()
        time.sleep(1)
        if input_thread.is_alive() is False:
            break
    
    # Очищаем строку с таймером
    sys.stdout.write(" " * 20 + "\r")
    sys.stdout.flush()

    if input_thread.is_alive():
        return None  # Время вышло
    else:
        return result[0]

# --- 3. Класс для хранения состояния игры ---
class GameState:
    def __init__(self, player1_name, player2_name, all_clubs):
        self.players = {0: player1_name, 1: player2_name}
        self.scores = {0: 0, 1: 0}
        
        # Эта строка гарантирует уникальный список клубов на игру
        self.game_clubs = random.sample(list(all_clubs.keys()), TOTAL_ROUNDS)
        self.all_clubs_data = all_clubs
        
        self.current_round = -1
        self.current_player_index = 0
        
        self.current_club_name = None
        self.current_club_players_original = [] # Для итогового списка
        self.current_club_players_normalized = [] # Для сравнения
        self.named_players = set()

    def start_new_round(self):
        self.current_round += 1
        if self.is_game_over():
            return False

        self.current_club_name = self.game_clubs[self.current_round]
        # Сохраняем оригинальные фамилии для вывода в конце раунда
        self.current_club_players_original = sorted(list(self.all_clubs_data[self.current_club_name]))
        # А для проверки будем использовать "нормализованные" фамилии
        self.current_club_players_normalized = [p.strip().capitalize() for p in self.current_club_players_original]
        
        self.named_players = set()
        self.current_player_index = self.current_round % 2
        return True

    def process_guess(self, guess):
        normalized_guess = guess.strip().capitalize()
        if normalized_guess in self.named_players:
            return 'already_named'
        if normalized_guess in self.current_club_players_normalized:
            self.named_players.add(normalized_guess)
            self.switch_player()
            return 'correct'
        return 'not_found'

    def switch_player(self):
        self.current_player_index = 1 - self.current_player_index

    def give_point_to_opponent(self):
        opponent_index = 1 - self.current_player_index
        self.scores[opponent_index] += 1
        print(f"\nИгрок {self.players[self.current_player_index]} не успел ответить! Очко получает {self.players[opponent_index]}.")

    def is_round_over(self):
        return len(self.named_players) == len(self.current_club_players_normalized)

    def is_game_over(self):
        if self.current_round >= TOTAL_ROUNDS:
            return True
        rounds_left = TOTAL_ROUNDS - (self.current_round) # Исправлен подсчет
        score_diff = abs(self.scores[0] - self.scores[1])
        if score_diff > rounds_left:
            print("\nДосрочное завершение: один из игроков уже не может отыграться.")
            return True
        return False
    
    def get_winner(self):
        if self.scores[0] > self.scores[1]: return self.players[0]
        elif self.scores[1] > self.scores[0]: return self.players[1]
        else: return "Ничья"

# --- НОВИНКА: Функция для отображения итогов раунда ---
def display_round_summary(game):
    """Показывает список игроков клуба, отмечая названных и неназванных."""
    print("\n" + "-"*15 + f" Итоги тура: {game.current_club_name} " + "-"*15)
    
    for player_surname in game.current_club_players_original:
        normalized_surname = player_surname.strip().capitalize()
        if normalized_surname in game.named_players:
            # В консоли имитируем цвета символами. В Telegram будут эмодзи ✅
            print(f"[✓] {player_surname}") 
        else:
            # В Telegram будут эмодзи ❌
            print(f"[✗] {player_surname}")
    
    print("-" * (32 + len(game.current_club_name)))
    print(f"Следующий раунд начнется через {PAUSE_BETWEEN_ROUNDS} секунд...")
    time.sleep(PAUSE_BETWEEN_ROUNDS)

# --- 4. Основная логика для симуляции игры в консоли (обновленная) ---
def main_console_game():
    all_clubs = load_player_data(PLAYERS_FILENAME)
    if not all_clubs: return

    game = GameState("Игрок 1", "Игрок 2", all_clubs)
    print(f"Начинается игра между '{game.players[0]}' и '{game.players[1]}'!")
    print("-" * 30)

    while True: # Бесконечный цикл, который прервется изнутри
        if not game.start_new_round():
            break # Выходим, если игра окончена

        print(f"\n\n--- ТУР {game.current_round + 1}/{TOTAL_ROUNDS} ---")
        print(f"Текущий клуб: {game.current_club_name}")
        print(f"Всего игроков в составе: {len(game.current_club_players_normalized)}")
        print(f"Текущий счет: {game.players[0]} {game.scores[0]} - {game.scores[1]} {game.players[1]}")
        
        round_timed_out = False
        while not game.is_round_over():
            current_player_name = game.players[game.current_player_index]
            named_count = len(game.named_players)
            total_count = len(game.current_club_players_normalized)
            time_limit = TIME_FIRST_HALF if named_count < total_count / 2 else TIME_SECOND_HALF
            
            prompt = f"\nХод игрока '{current_player_name}'. Введите фамилию: "
            guess = get_input_with_countdown(prompt, time_limit)
            
            if guess is None: # Если функция вернула None, значит время вышло
                game.give_point_to_opponent()
                round_timed_out = True
                break # Завершаем раунд досрочно

            result = game.process_guess(guess)
            if result == 'correct': print("Верно!")
            elif result == 'already_named': print("Эту фамилию уже называли.")
            elif result == 'not_found': print("Неверная фамилия.")
        
        if not round_timed_out and game.is_round_over():
            print(f"\nВсе игроки клуба '{game.current_club_name}' названы!")

        # Показываем итоги раунда
        display_round_summary(game)

    # --- Конец игры ---
    print("\n" + "=" * 30)
    print("ИГРА ОКОНЧЕНА!")
    print(f"Итоговый счет: {game.players[0]} {game.scores[0]} - {game.scores[1]} {game.players[1]}")
    winner = game.get_winner()
    if winner == "Ничья": print("Результат: Ничья!")
    else: print(f"Победитель: {winner}!")
    print("=" * 30)

if __name__ == "__main__":
    main_console_game()