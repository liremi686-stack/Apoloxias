import os

def print_tree(directory, prefix=""):
    """
    Рекурсивно выводит дерево папок и файлов.
    
    Аргументы:
        directory (str): путь к текущей директории
        prefix (str): строка-префикс для форматирования отступов
    """
    try:
        items = os.listdir(directory)
    except PermissionError:
        # Если нет доступа к папке, пропускаем её
        return

    # Сортируем для детерминированного вывода
    items.sort()

    for i, item in enumerate(items):
        is_last = (i == len(items) - 1)          # последний элемент в текущей папке?
        connector = "└── " if is_last else "├── "
        full_path = os.path.join(directory, item)

        # Добавляем '/' в конце для папок
        if os.path.isdir(full_path):
            name = item + "/"
        else:
            name = item

        print(prefix + connector + name)

        # Если это папка, обходим её рекурсивно
        if os.path.isdir(full_path):
            extension = "    " if is_last else "│   "
            print_tree(full_path, prefix + extension)


def main():
    import sys

    # Получаем путь из аргументов командной строки или запрашиваем ввод
    if len(sys.argv) > 1:
        root_dir = sys.argv[1]
    else:
        root_dir = input("Введите путь к папке: ").strip()

    # Проверяем существование
    if not os.path.isdir(root_dir):
        print("Ошибка: указанная папка не существует или не является директорией.")
        return

    # Выводим корневую папку
    print(os.path.basename(root_dir) + "/")
    print_tree(root_dir)


if __name__ == "__main__":
    main()