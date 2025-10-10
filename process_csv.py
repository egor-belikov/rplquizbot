import pandas as pd
from bs4 import BeautifulSoup

def parse_local_html_file(local_filename="rplquizbot/index.html", output_filename="rpl_players_from_local_file.xlsx"):
    """
    Парсит локальный HTML-файл (index.html), извлекает данные об игроках
    и сохраняет результат в файл Excel (.xlsx).
    """
    try:
        # Открываем и читаем локальный HTML-файл с указанием кодировки utf-8
        with open(local_filename, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Ошибка: файл '{local_filename}' не найден.")
        print("Убедитесь, что вы сохранили веб-страницу как 'index.html' в той же папке, где находится скрипт.")
        return
    except Exception as e:
        print(f"Произошла ошибка при чтении файла: {e}")
        return

    # Используем BeautifulSoup для парсинга HTML-содержимого файла
    soup = BeautifulSoup(html_content, 'lxml')
    
    all_players_data = []

    # Находим все заголовки H2, которые являются названиями клубов
    club_headers = soup.find_all('h2')

    for header in club_headers:
        club_name_span = header
        if not club_name_span:
            continue
        
        # Получаем чистое название клуба
        cleantext = BeautifulSoup(header, "lxml").text

        club_name = cleantext # club_name_span.get_text().strip()

        # Ищем следующую за заголовком таблицу с классом 'wikitable'
        team_table = header.find_next_sibling('table', class_='wikitable')
        
        if team_table:
            # Проходим по всем строкам таблицы, пропуская заголовок (первую строку)
            rows = team_table.find_all('tr')[1:]
            for row in rows:
                cols = row.find_all('td')
                
                # Проверяем, что в строке есть ячейки
                if len(cols) > 1:
                    # --- Ключевое изменение ---
                    # Имя игрока находится во ВТОРОМ столбце (индекс 1)
                    player_name_cell = cols[1].find('a') 
                    
                    if player_name_cell:
                        player_name = player_name_cell.get_text().strip()
                        
                        # Пропускаем строки, где в имени игрока может быть тренер
                        if player_name.lower() not in ["тренер"]:
                            all_players_data.append({'Клуб': club_name, 'Имя игрока': player_name})

    if not all_players_data:
        print("Не удалось найти данные игроков. Проверьте структуру HTML-файла.")
        return

    # Создаем DataFrame и сохраняем его в Excel
    df = pd.DataFrame(all_players_data)

    try:
        df.to_excel(output_filename, index=False)
        print(f"Файл '{output_filename}' успешно создан!")
    except Exception as e:
        print(f"Произошла ошибка при сохранении файла Excel: {e}")


if __name__ == '__main__':
    # Вызываем функцию для парсинга локального файла index.html
    parse_local_html_file()