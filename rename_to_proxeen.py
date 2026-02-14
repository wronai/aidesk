#!/usr/bin/env python3
"""
Skrypt do zamiany wszystkich wystąpień 'proxeen' na 'proxeen' z zachowaniem wielkości liter.
Obsługuje nazwy folderów, nazwy plików oraz treści w plikach.
"""

import os
import re
import sys
import shutil
from pathlib import Path
from typing import List, Tuple

# Mapowania zamian z zachowaniem wielkości liter
REPLACEMENTS = {
    'PROXEEN': 'PROXEEN',
    'proxeen': 'proxeen',
    'Proxeen': 'Proxeen',
    'Proxeen': 'Proxeen',
    'Proxeen': 'Proxeen',
    'proxeEN': 'proxeEN',
    'PROXEen': 'PROXEen',
    'pROXEEN': 'pROXEEN'
}

def replace_case_preserving(text: str) -> str:
    """Zamienia wszystkie wystąpienia proxeen na proxeen z zachowaniem wielkości liter."""
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text

def find_files_and_dirs(root_dir: Path) -> Tuple[List[Path], List[Path]]:
    """Znajduje wszystkie pliki i foldery do zmiany nazwy."""
    files_to_rename = []
    dirs_to_rename = []
    
    # Najpierw zbieramy wszystkie ścieżki
    for item in root_dir.rglob('*'):
        if item.is_file() or item.is_dir():
            # Sprawdzamy czy nazwa zawiera proxeen
            if any(proxeen_variant in item.name for proxeen_variant in REPLACEMENTS.keys()):
                if item.is_file():
                    files_to_rename.append(item)
                else:
                    dirs_to_rename.append(item)
    
    # Sortujemy foldery od najgłębszych do najpłytszych
    dirs_to_rename.sort(key=lambda x: len(x.parts), reverse=True)
    
    return files_to_rename, dirs_to_rename

def rename_files_and_dirs(files_to_rename: List[Path], dirs_to_rename: List[Path]) -> bool:
    """Zmienia nazwy plików i folderów."""
    try:
        # Najpierw zmieniamy nazwy plików
        for file_path in files_to_rename:
            new_name = replace_case_preserving(file_path.name)
            new_path = file_path.parent / new_name
            
            if new_path != file_path:
                print(f"Zmiana nazwy pliku: {file_path} -> {new_path}")
                file_path.rename(new_path)
        
        # Potem zmieniamy nazwy folderów (od najgłębszych)
        for dir_path in dirs_to_rename:
            new_name = replace_case_preserving(dir_path.name)
            new_path = dir_path.parent / new_name
            
            if new_path != dir_path:
                print(f"Zmiana nazwy folderu: {dir_path} -> {new_path}")
                dir_path.rename(new_path)
        
        return True
    except Exception as e:
        print(f"Błąd podczas zmiany nazw: {e}")
        return False

def update_file_contents(root_dir: Path, extensions: List[str] = None) -> bool:
    """Aktualizuje treść plików."""
    if extensions is None:
        extensions = ['.py', '.md', '.txt', '.json', '.yaml', '.yml', '.sh', '.bat', 
                     '.js', '.html', '.css', '.ts', '.tsx', '.json', '.toml']
    
    try:
        for file_path in root_dir.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                try:
                    # Czytamy plik jako tekst
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Sprawdzamy czy są zmiany
                    new_content = replace_case_preserving(content)
                    
                    if new_content != content:
                        print(f"Aktualizacja treści pliku: {file_path}")
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                
                except (UnicodeDecodeError, PermissionError) as e:
                    print(f"Pomijanie pliku {file_path}: {e}")
                    continue
        
        return True
    except Exception as e:
        print(f"Błąd podczas aktualizacji treści: {e}")
        return False

def create_backup(root_dir: Path) -> bool:
    """Tworzy kopię zapasową."""
    backup_dir = root_dir.parent / f"{root_dir.name}_backup"
    
    try:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        
        print(f"Tworzenie kopii zapasowej: {backup_dir}")
        shutil.copytree(root_dir, backup_dir)
        return True
    except Exception as e:
        print(f"Błąd podczas tworzenia kopii zapasowej: {e}")
        return False

def main():
    """Główna funkcja skryptu."""
    root_dir = Path.cwd()
    
    print(f"Rozpoczynanie zamiany 'proxeen' na 'proxeen' w: {root_dir}")
    print("Mapowanie zamian:")
    for old, new in REPLACEMENTS.items():
        print(f"  {old} -> {new}")
    
    # Tworzenie kopii zapasowej
    print("\n1. Tworzenie kopii zapasowej...")
    if not create_backup(root_dir):
        print("Nie udało się utworzyć kopii zapasowej. Przerywam.")
        sys.exit(1)
    
    # Znajdowanie plików i folderów do zmiany
    print("\n2. Wyszukiwanie plików i folderów do zmiany nazwy...")
    files_to_rename, dirs_to_rename = find_files_and_dirs(root_dir)
    
    print(f"Znaleziono {len(files_to_rename)} plików i {len(dirs_to_rename)} folderów do zmiany nazwy")
    
    # Zmiana nazw plików i folderów
    print("\n3. Zmiana nazw plików i folderów...")
    if not rename_files_and_dirs(files_to_rename, dirs_to_rename):
        print("Błąd podczas zmiany nazw. Sprawdź kopię zapasową.")
        sys.exit(1)
    
    # Aktualizacja treści plików
    print("\n4. Aktualizacja treści plików...")
    if not update_file_contents(root_dir):
        print("Błąd podczas aktualizacji treści. Sprawdź kopię zapasową.")
        sys.exit(1)
    
    print("\n✅ Zamiana zakończona pomyślnie!")
    print(f"Kopia zapasowa dostępna w: {root_dir.parent / f'{root_dir.name}_backup'}")

if __name__ == "__main__":
    main()
