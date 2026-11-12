#!/home/sergio/playlist/.venv/bin/python
"""
Assistente e CLI para criar playlists no YouTube a partir de arquivos .txt, .json
ou argumentos de linha de comando, usando busca automática via yt-dlp e OAuth 2.0
(YouTube Data API v3) ou fila rápida no navegador.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yt_dlp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube"]
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CLIENT_SECRETS = BASE_DIR / "client_secret.json"
DEFAULT_TOKEN_FILE = BASE_DIR / "token.json"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_URL_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})"
)
TITLE_PREFIX_RE = re.compile(
    r"^(?:playlist|t[íi]tulo|title|nome)\s*:\s*(.+)$", re.IGNORECASE
)
PRIVACY_PREFIX_RE = re.compile(
    r"^(?:privacidade|privacy)\s*:\s*(public|p[úu]blica|unlisted|n[ãa]o\s*listada|private|privada)\s*$",
    re.IGNORECASE,
)
SECTION_HEADER_RE = re.compile(
    r"^(?:bloco|parte|ato|se[çc][ãa]o|cap[íi]tulo|introdu[çc][ãa]o|conclus[ãa]o|obs(?:erva[çc][ãa]o)?|nota|o\s+que\s+observar|coment[áa]rio|descri[çc][ãa]o)\b",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^(?:[•\-\*\+]|\d+[\.\)])\s*(.+)$")


def normalize_privacy(val: str) -> str:
    v = val.strip().lower()
    if v in ("public", "pública", "publica"):
        return "public"
    if v in ("unlisted", "não listada", "nao listada"):
        return "unlisted"
    if v in ("private", "privada"):
        return "private"
    return "public"


def clean_track_query(raw_track: str) -> str:
    """Remove anotações parentéticas longas como '(Álbum: ...)' para melhorar a busca, mantendo o nome original para exibição."""
    cleaned = re.sub(
        r"\(\s*(?:[áa]lbum|album|disco|ano|faixa)\s*:[^)]+\)",
        "",
        raw_track,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or raw_track.strip()


def parse_playlist_txt(txt_path: Path) -> tuple[str, str, str, list[str]]:
    """
    Lê um arquivo .txt de playlist (simples ou anotado com blocos/observações)
    e extrai: (título, descrição, privacidade, lista_de_faixas).
    """
    raw_content = txt_path.read_text(encoding="utf-8")
    lines = raw_content.splitlines()

    title: str | None = None
    privacy = "public"
    tracks: list[str] = []
    description_lines: list[str] = []
    has_rich_structure = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            description_lines.append("")
            continue

        # 1. Cabeçalho explícito de título ("Playlist: ...", "Título: ...", "# ...")
        title_match = TITLE_PREFIX_RE.match(stripped)
        if title_match and title is None:
            title = title_match.group(1).strip()
            continue
        if stripped.startswith("# ") and title is None:
            title = stripped[2:].strip()
            continue

        # 2. Cabeçalho explícito de privacidade
        priv_match = PRIVACY_PREFIX_RE.match(stripped)
        if priv_match:
            privacy = normalize_privacy(priv_match.group(1))
            continue

        # 3. Linhas indentadas (com tab ou >= 2 espaços) que são sub-bullets de observação
        is_indented = raw_line.startswith("\t") or raw_line.startswith("  ")
        bullet_match = BULLET_RE.match(stripped)
        inner_text = bullet_match.group(1).strip() if bullet_match else stripped

        if SECTION_HEADER_RE.match(inner_text):
            has_rich_structure = True
            description_lines.append(raw_line.rstrip())
            continue

        if is_indented and bullet_match and (" – " not in inner_text and " - " not in inner_text):
            has_rich_structure = True
            description_lines.append(raw_line.rstrip())
            continue

        # 4. Linha de música (bullet de 1º nível ou linha com Artista - Música / URL / ID)
        tracks.append(inner_text)
        description_lines.append(raw_line.rstrip())

    if not title:
        title = txt_path.stem.replace("_", " ").replace("-", " ").title()

    description = "\n".join(description_lines).strip() if has_rich_structure else ""
    return title, description, privacy, tracks


def print_oauth_setup_help(client_secrets_path: Path) -> None:
    print(
        f"\n┌──────────────────────────────────────────────────────────────────────────┐\n"
        f"│ COMO CONFIGURAR O LOGIN OAUTH DO YOUTUBE (APENAS NA 1ª VEZ)             │\n"
        f"└──────────────────────────────────────────────────────────────────────────┘\n"
        f"Arquivo ausente: {client_secrets_path}\n\n"
        f"Passo a passo rápido:\n"
        f"  1. Acesse: https://console.cloud.google.com/apis/library/youtube.googleapis.com\n"
        f"     e clique em 'ATIVAR' para habilitar a YouTube Data API v3.\n"
        f"  2. Acesse: https://console.cloud.google.com/apis/credentials\n"
        f"     e clique em '+ CRIAR CREDENCIAIS' -> 'ID do cliente OAuth'.\n"
        f"  3. Se pedir para configurar a Tela de Permissão, escolha 'Externo',\n"
        f"     preencha nome/e-mail e adicione seu e-mail em 'Usuários de teste'.\n"
        f"  4. Em Tipo de aplicativo, escolha 'App para computador' (Desktop App).\n"
        f"  5. Baixe o arquivo JSON e salve-o exatamente como:\n"
        f"     {client_secrets_path}\n",
        file=sys.stderr,
    )


def get_authenticated_service(
    client_secrets_path: Path, token_path: Path
) -> Any:
    """Autentica o usuário via OAuth 2.0 e retorna o cliente da YouTube Data API v3."""
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[*] Atualizando token OAuth expirado...")
            creds.refresh(Request())
        else:
            if not client_secrets_path.exists():
                print_oauth_setup_help(client_secrets_path)
                raise FileNotFoundError(f"Arquivo não encontrado: {client_secrets_path}")
            print("[*] Abrindo navegador para autenticação OAuth 2.0 no Google...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)

        token_path.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(token_path, 0o600)
        print(f"[+] Token salvo em {token_path}")

    return build("youtube", "v3", credentials=creds)


def resolve_video_id(query_or_id: str) -> dict[str, str]:
    """Resolve um ID de 11 caracteres, URL do YouTube ou termo de busca para um vídeo."""
    text = query_or_id.strip()

    url_match = YOUTUBE_URL_RE.search(text)
    if url_match:
        vid = url_match.group(1)
        return {"id": vid, "title": text, "channel": "URL direta", "query": text}

    if VIDEO_ID_RE.match(text):
        return {"id": text, "title": text, "channel": "ID direto", "query": text}

    search_query = clean_track_query(text)
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{search_query}", download=False)
        entries = info.get("entries") if info else None
        if not entries:
            raise ValueError(f"Nenhum vídeo encontrado para: {text!r}")
        entry = entries[0]
        return {
            "id": entry["id"],
            "title": entry.get("title") or text,
            "channel": entry.get("channel") or entry.get("uploader") or "Desconhecido",
            "query": text,
        }


def create_youtube_playlist(
    youtube: Any, title: str, description: str, privacy: str
) -> str:
    """Cria a playlist na conta autenticada e retorna o playlistId."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
        },
        "status": {
            "privacyStatus": privacy,
        },
    }
    response = (
        youtube.playlists()
        .insert(part="snippet,status", body=body)
        .execute()
    )
    return response["id"]


def add_video_to_playlist(youtube: Any, playlist_id: str, video_id: str) -> None:
    """Insere um vídeo na playlist especificada."""
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        }
    }
    youtube.playlistItems().insert(part="snippet", body=body).execute()


def load_spec_from_json(json_path: Path) -> tuple[str, str, str, list[str]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    title = data.get("title") or "Nova Playlist"
    description = data.get("description") or ""
    privacy = normalize_privacy(data.get("privacy") or "public")
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list) or not tracks:
        raise ValueError(f"O arquivo {json_path} deve conter uma lista 'tracks' não vazia.")
    return title, description, privacy, [str(t) for t in tracks]


def discover_txt_files() -> list[Path]:
    """Busca arquivos .txt no diretório atual e em ~/playlist."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for folder in [Path.cwd(), BASE_DIR]:
        if folder.exists():
            for p in sorted(folder.glob("*.txt")):
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    candidates.append(p)
    return candidates


def create_sample_txt(target_path: Path) -> None:
    sample = (
        "Playlist: Minha Nova Playlist\n"
        "Privacidade: public\n\n"
        "Bloco 1: Clássicos\n"
        "• The Velvet Underground – What Goes On\n"
        "\t• O que observar: Comentário opcional que vai para a descrição da playlist.\n"
        "• Television – Marquee Moon\n"
        "• The Strokes – Reptilia\n"
    )
    target_path.write_text(sample, encoding="utf-8")


def run_interactive_wizard(
    initial_txt: Path | None,
    client_secrets: Path,
    token_file: Path,
) -> None:
    print("======================================================================")
    print("   ASSISTENTE DE CRIAÇÃO DE PLAYLISTS NO YOUTUBE (A PARTIR DE .TXT)  ")
    print("======================================================================\n")

    txt_path = initial_txt
    if not txt_path:
        txt_files = discover_txt_files()
        if txt_files:
            print("Arquivos .txt encontrados:")
            for idx, p in enumerate(txt_files, 1):
                print(f"  [{idx}] {p}")
            print("  [N] Criar um arquivo .txt modelo em branco")
            print("  [C] Digitar outro caminho de arquivo .txt\n")
            choice = input("Escolha uma opção (Enter para [1]): ").strip() or "1"
            if choice.isdigit() and 1 <= int(choice) <= len(txt_files):
                txt_path = txt_files[int(choice) - 1]
            elif choice.lower() == "n":
                new_name = input("Nome do novo arquivo (ex: minha_playlist.txt): ").strip() or "minha_playlist.txt"
                if not new_name.endswith(".txt"):
                    new_name += ".txt"
                txt_path = BASE_DIR / new_name
                create_sample_txt(txt_path)
                print(f"\n[+] Modelo criado em: {txt_path}")
                print("    Você pode editá-lo agora em outro terminal/editor se quiser.")
                input("    Pressione Enter quando estiver pronto para continuar...")
            else:
                raw_p = input("Digite o caminho do arquivo .txt: ").strip()
                txt_path = Path(raw_p).expanduser()
        else:
            raw_p = input("Digite o caminho do arquivo .txt (ou Enter para criar um modelo): ").strip()
            if not raw_p:
                txt_path = BASE_DIR / "minha_playlist.txt"
                create_sample_txt(txt_path)
                print(f"\n[+] Modelo criado em: {txt_path}")
                input("    Edite o arquivo se desejar e pressione Enter para continuar...")
            else:
                txt_path = Path(raw_p).expanduser()

    if not txt_path.exists():
        print(f"[!] Arquivo não encontrado: {txt_path}", file=sys.stderr)
        sys.exit(1)

    # Passo 2: Analisar o .txt e confirmar metadados
    title, description, privacy, tracks = parse_playlist_txt(txt_path)

    print("\n----------------------------------------------------------------------")
    print(f"Arquivo lido : {txt_path}")
    print(f"Título       : {title}")
    print(f"Privacidade  : {privacy}")
    print(f"Descrição    : {len(description.splitlines())} linha(s) extraída(s)")
    print(f"Faixas ({len(tracks)})   :")
    for idx, t in enumerate(tracks, 1):
        print(f"  {idx:2d}. {t}")
    print("----------------------------------------------------------------------")

    if not tracks:
        print("[!] Nenhuma faixa foi identificada no arquivo .txt.", file=sys.stderr)
        sys.exit(1)

    new_title = input(f"\nTítulo da playlist [Enter para manter '{title}']: ").strip()
    if new_title:
        title = new_title

    new_priv = input(
        f"Privacidade (public / unlisted / private) [Enter para manter '{privacy}']: "
    ).strip()
    if new_priv:
        privacy = normalize_privacy(new_priv)

    # Passo 3: Resolver vídeos via yt-dlp
    print(f"\n[*] Buscando os {len(tracks)} vídeos no YouTube...")
    resolved: list[dict[str, str]] = []
    for idx, track in enumerate(tracks, 1):
        info = resolve_video_id(track)
        resolved.append(info)
        print(
            f"  {idx:2d}. [{info['id']}] {info['title']} ({info['channel']})\n"
            f"      Link: https://www.youtube.com/watch?v={info['id']}"
        )

    # Permitir substituição interativa de qualquer faixa
    while True:
        swap = input(
            "\nDeseja substituir alguma faixa por outro termo/URL? "
            "(Digite o número da faixa ou Enter para confirmar todas): "
        ).strip()
        if not swap:
            break
        if swap.isdigit() and 1 <= int(swap) <= len(resolved):
            i = int(swap) - 1
            new_q = input(f"Novo termo de busca ou URL/ID para a faixa #{i + 1}: ").strip()
            if new_q:
                info = resolve_video_id(new_q)
                resolved[i] = info
                print(f"  -> Atualizado #{i + 1}: [{info['id']}] {info['title']} ({info['channel']})")
        else:
            print("  [!] Número inválido.")

    video_ids_csv = ",".join(item["id"] for item in resolved)
    queue_url = f"https://www.youtube.com/watch_videos?video_ids={video_ids_csv}"

    # Passo 4: Escolher como criar
    oauth_ready = client_secrets.exists() or token_file.exists()
    oauth_status = "Configurado" if oauth_ready else "Requer client_secret.json"

    print("\n----------------------------------------------------------------------")
    print("Como você deseja criar a playlist agora?")
    print("  [1] Abrir fila pronta no navegador (rápido, sem precisar de API/OAuth)")
    print(f"  [2] Criar direto na sua conta via OAuth API ({oauth_status})")
    print("  [3] Salvar definição como arquivo .json e abrir fila no navegador")
    print("----------------------------------------------------------------------")
    action = input("Escolha [1/2/3] (Enter para [1]): ").strip() or "1"

    if action in ("1", "3"):
        if action == "3":
            json_out = txt_path.with_suffix(".json")
            json_out.write_text(
                json.dumps(
                    {
                        "title": title,
                        "privacy": privacy,
                        "description": description,
                        "tracks": [item["id"] for item in resolved],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\n[+] Arquivo JSON salvo em: {json_out}")

        print(f"\n[+] Abrindo fila no navegador:\n    {queue_url}")
        subprocess.Popen(
            ["xdg-open", queue_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("\n=== TÍTULO PARA COPIAR AO SALVAR ===")
        print(title)
        if description:
            print("\n=== DESCRIÇÃO PARA COPIAR AO SALVAR ===")
            print(description)
        return

    if action == "2":
        if not oauth_ready:
            print_oauth_setup_help(client_secrets)
            ans = input(
                "Deseja abrir o Google Cloud Console no navegador agora para baixar o client_secret.json? [s/N]: "
            ).strip().lower()
            if ans == "s":
                subprocess.Popen(
                    ["xdg-open", "https://console.cloud.google.com/apis/credentials"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                input(f"\nSalve o arquivo em {client_secrets} e pressione Enter para continuar...")

        try:
            youtube = get_authenticated_service(client_secrets, token_file)
            print(f"\n[*] Criando playlist '{title}' ({privacy})...")
            playlist_id = create_youtube_playlist(youtube, title, description, privacy)
            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            for idx, item in enumerate(resolved, 1):
                print(f"  -> Adicionando ({idx}/{len(resolved)}): {item['title']}...")
                add_video_to_playlist(youtube, playlist_id, item["id"])
            print(f"\n[✓] Playlist criada com sucesso!\n    {playlist_url}")
            subprocess.Popen(
                ["xdg-open", playlist_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"\n[!] Não foi possível criar via API: {exc}", file=sys.stderr)
            print(f"[*] Abrindo a fila direta no navegador como alternativa:\n    {queue_url}")
            subprocess.Popen(
                ["xdg-open", queue_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def run_gui_app(
    initial_txt: Path | None,
    client_secrets: Path,
    token_file: Path,
) -> None:
    """Interface gráfica (Tkinter) para carregar um .txt, buscar vídeos e criar a playlist."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Assistente de Playlists do YouTube (.txt)")
    root.geometry("880x700")

    resolved_items: list[dict[str, str]] = []

    top_frame = ttk.Frame(root, padding=10)
    top_frame.pack(fill=tk.X)

    file_var = tk.StringVar(value=str(initial_txt) if initial_txt else "")
    title_var = tk.StringVar()
    privacy_var = tk.StringVar(value="public")

    def load_txt_path(p: Path) -> None:
        if not p.exists():
            messagebox.showerror("Erro", f"Arquivo não encontrado:\n{p}")
            return
        file_var.set(str(p))
        t, desc, priv, trks = parse_playlist_txt(p)
        title_var.set(t)
        privacy_var.set(priv)
        desc_text.delete("1.0", tk.END)
        desc_text.insert(tk.END, desc)
        tracks_text.delete("1.0", tk.END)
        tracks_text.insert(tk.END, "\n".join(trks))
        results_list.delete(0, tk.END)
        resolved_items.clear()
        status_var.set(f"Arquivo carregado: {len(trks)} faixas detectadas. Clique em '1. Buscar Vídeos no YouTube'.")

    def browse_txt() -> None:
        filename = filedialog.askopenfilename(
            initialdir=str(BASE_DIR),
            title="Selecione o arquivo .txt da playlist",
            filetypes=[("Arquivos de texto", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if filename:
            load_txt_path(Path(filename))

    ttk.Label(top_frame, text="Arquivo .txt:").grid(row=0, column=0, sticky=tk.W, pady=3)
    ttk.Entry(top_frame, textvariable=file_var, width=65).grid(row=0, column=1, sticky=tk.EW, padx=5)
    ttk.Button(top_frame, text="Selecionar .txt...", command=browse_txt).grid(row=0, column=2, padx=4)

    ttk.Label(top_frame, text="Título da Playlist:").grid(row=1, column=0, sticky=tk.W, pady=3)
    ttk.Entry(top_frame, textvariable=title_var, width=65).grid(row=1, column=1, sticky=tk.EW, padx=5)

    priv_frame = ttk.Frame(top_frame)
    priv_frame.grid(row=1, column=2, sticky=tk.W)
    ttk.Label(priv_frame, text="Privacidade:").pack(side=tk.LEFT)
    ttk.Combobox(
        priv_frame,
        textvariable=privacy_var,
        values=["public", "unlisted", "private"],
        width=10,
        state="readonly",
    ).pack(side=tk.LEFT, padx=4)

    top_frame.columnconfigure(1, weight=1)

    mid_pane = ttk.PanedWindow(root, orient=tk.VERTICAL)
    mid_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    tracks_frame = ttk.LabelFrame(mid_pane, text="Faixas para Buscar (1 por linha)", padding=6)
    tracks_text = tk.Text(tracks_frame, height=8, wrap=tk.NONE)
    tracks_text.pack(fill=tk.BOTH, expand=True)
    mid_pane.add(tracks_frame, weight=1)

    desc_frame = ttk.LabelFrame(mid_pane, text="Descrição da Playlist (extraída do .txt)", padding=6)
    desc_text = tk.Text(desc_frame, height=7, wrap=tk.WORD)
    desc_text.pack(fill=tk.BOTH, expand=True)
    mid_pane.add(desc_frame, weight=1)

    res_frame = ttk.LabelFrame(mid_pane, text="Vídeos Encontrados no YouTube", padding=6)
    results_list = tk.Listbox(res_frame, height=8)
    results_list.pack(fill=tk.BOTH, expand=True)
    mid_pane.add(res_frame, weight=1)

    status_var = tk.StringVar(value="Selecione um arquivo .txt para começar.")
    ttk.Label(root, textvariable=status_var, padding=(10, 2)).pack(fill=tk.X)

    btn_frame = ttk.Frame(root, padding=10)
    btn_frame.pack(fill=tk.X)

    def resolve_all() -> None:
        raw_lines = [
            ln.strip()
            for ln in tracks_text.get("1.0", tk.END).splitlines()
            if ln.strip()
        ]
        if not raw_lines:
            messagebox.showwarning("Aviso", "Informe ao menos uma faixa na caixa de faixas.")
            return
        results_list.delete(0, tk.END)
        resolved_items.clear()
        for idx, track in enumerate(raw_lines, 1):
            status_var.set(f"Buscando ({idx}/{len(raw_lines)}): {track}...")
            root.update_idletasks()
            try:
                info = resolve_video_id(track)
                resolved_items.append(info)
                results_list.insert(
                    tk.END,
                    f"{idx:2d}. [{info['id']}] {info['title']} ({info['channel']})",
                )
            except Exception as exc:
                messagebox.showerror("Erro na busca", f"Falha ao buscar '{track}':\n{exc}")
                return
        status_var.set(f"Pronto! {len(resolved_items)} vídeos encontrados. Escolha como criar a playlist.")

    def open_queue() -> None:
        if not resolved_items:
            resolve_all()
        if not resolved_items:
            return
        ids_csv = ",".join(item["id"] for item in resolved_items)
        url = f"https://www.youtube.com/watch_videos?video_ids={ids_csv}"
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status_var.set("Fila aberta no navegador!")

    def create_oauth() -> None:
        if not resolved_items:
            resolve_all()
        if not resolved_items:
            return
        try:
            status_var.set("Autenticando via OAuth 2.0...")
            root.update_idletasks()
            youtube = get_authenticated_service(client_secrets, token_file)
            title = title_var.get().strip() or "Nova Playlist"
            desc = desc_text.get("1.0", tk.END).strip()
            priv = privacy_var.get().strip() or "public"
            pid = create_youtube_playlist(youtube, title, desc, priv)
            for idx, item in enumerate(resolved_items, 1):
                status_var.set(f"Adicionando ({idx}/{len(resolved_items)}): {item['title']}...")
                root.update_idletasks()
                add_video_to_playlist(youtube, pid, item["id"])
            url = f"https://www.youtube.com/playlist?list={pid}"
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            status_var.set(f"Playlist criada com sucesso: {url}")
            messagebox.showinfo("Sucesso", f"Playlist criada na sua conta!\n\n{url}")
        except FileNotFoundError:
            messagebox.showwarning(
                "Configuração OAuth Pendente",
                f"Salve suas credenciais OAuth (Desktop App) em:\n{client_secrets}\n\n"
                "Enquanto isso, você pode usar o botão '2A. Abrir Fila no Navegador'.",
            )
        except Exception as exc:
            messagebox.showerror("Erro na API", str(exc))

    ttk.Button(btn_frame, text="1. Buscar Vídeos no YouTube", command=resolve_all).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="2A. Abrir Fila no Navegador (Sem API)", command=open_queue).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="2B. Criar na Minha Conta (OAuth API)", command=create_oauth).pack(side=tk.LEFT, padx=5)

    if initial_txt and initial_txt.exists():
        load_txt_path(initial_txt)

    root.mainloop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assistente e CLI para criar playlists no YouTube a partir de arquivos .txt, .json ou termos de busca."
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Inicia o assistente interativo passo a passo no terminal",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Abre a interface gráfica (janela Tkinter) para criar a playlist a partir de um .txt",
    )
    parser.add_argument(
        "--from-txt",
        type=Path,
        help="Carrega título, descrição e faixas de um arquivo .txt (simples ou com blocos/observações)",
    )
    parser.add_argument(
        "-f", "--from-json",
        type=Path,
        help="Carrega título, descrição, privacidade e faixas de um arquivo JSON",
    )
    parser.add_argument(
        "-t", "--title",
        help="Título da playlist (sobrescreve o título do arquivo)",
    )
    parser.add_argument(
        "-d", "--description",
        default="",
        help="Descrição da playlist (sobrescreve a descrição do arquivo)",
    )
    parser.add_argument(
        "-p", "--privacy",
        choices=["public", "unlisted", "private"],
        default=None,
        help="Privacidade da playlist (public, unlisted, private)",
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=Path(os.environ.get("YT_CLIENT_SECRETS", DEFAULT_CLIENT_SECRETS)),
        help=f"Caminho para o client_secret.json (padrão: {DEFAULT_CLIENT_SECRETS})",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(os.environ.get("YT_TOKEN_FILE", DEFAULT_TOKEN_FILE)),
        help=f"Caminho para salvar/ler o token OAuth (padrão: {DEFAULT_TOKEN_FILE})",
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Apenas executa o fluxo de login OAuth e salva o token.json",
    )
    parser.add_argument(
        "--queue-only",
        action="store_true",
        help="Apenas resolve os vídeos via yt-dlp e gera/abre a fila sem chamar a API",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Abre a playlist ou fila criada no navegador ao finalizar",
    )
    parser.add_argument(
        "tracks",
        nargs="*",
        help="Arquivo .txt/.json ou lista de músicas (termos de busca, URLs ou IDs de vídeo)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.auth_only:
        get_authenticated_service(args.client_secrets, args.token_file)
        print("[+] Autenticação concluída com sucesso!")
        return

    # Se o único argumento posicional for um arquivo .txt existente, trata automaticamente
    auto_txt: Path | None = args.from_txt
    if not auto_txt and len(args.tracks) == 1 and args.tracks[0].endswith(".txt"):
        candidate = Path(args.tracks[0]).expanduser()
        if candidate.exists():
            auto_txt = candidate
            args.tracks = []

    if args.gui:
        run_gui_app(auto_txt, args.client_secrets, args.token_file)
        return

    # Se rodou sem argumentos ou pediu --interactive, abre o assistente passo a passo!
    if args.interactive or (
        not auto_txt and not args.from_json and not args.tracks
    ):
        run_interactive_wizard(auto_txt, args.client_secrets, args.token_file)
        return

    # Se passou apenas `yt-playlist arquivo.txt` sem flags de automação (--queue-only),
    # usa o assistente interativo já carregado com esse arquivo .txt!
    if auto_txt and not args.queue_only and not args.open and not args.title and not args.privacy:
        run_interactive_wizard(auto_txt, args.client_secrets, args.token_file)
        return

    if auto_txt:
        title, description, privacy, tracks = parse_playlist_txt(auto_txt)
    elif args.from_json:
        title, description, privacy, tracks = load_spec_from_json(args.from_json)
    else:
        title = "Nova Playlist"
        description = ""
        privacy = "public"
        tracks = args.tracks

    if args.title:
        title = args.title
    if args.description:
        description = args.description
    if args.privacy:
        privacy = args.privacy

    print(f"[*] Playlist : {title} ({privacy})")
    print(f"[*] Resolvendo {len(tracks)} faixa(s) no YouTube...")
    resolved: list[dict[str, str]] = []
    for idx, track in enumerate(tracks, 1):
        info = resolve_video_id(track)
        resolved.append(info)
        print(f"  {idx:2d}. [{info['id']}] {info['title']} ({info['channel']})")

    video_ids_csv = ",".join(item["id"] for item in resolved)
    queue_url = f"https://www.youtube.com/watch_videos?video_ids={video_ids_csv}"

    if args.queue_only:
        print(f"\n[+] URL da fila gerada:\n    {queue_url}")
        if args.open:
            subprocess.Popen(["xdg-open", queue_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    try:
        youtube = get_authenticated_service(args.client_secrets, args.token_file)
        print(f"\n[*] Criando playlist '{title}' ({privacy})...")
        playlist_id = create_youtube_playlist(youtube, title, description, privacy)
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        print(f"[+] Playlist criada! ID: {playlist_id}")

        for idx, item in enumerate(resolved, 1):
            print(f"  -> Adicionando ({idx}/{len(resolved)}): {item['title']}...")
            add_video_to_playlist(youtube, playlist_id, item["id"])

        print(f"\n[✓] Playlist '{title}' pronta com {len(resolved)} faixas!")
        print(f"    Link direto: {playlist_url}")

        if args.open:
            subprocess.Popen(["xdg-open", playlist_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    except (HttpError, FileNotFoundError) as err:
        print(f"\n[!] Não foi possível criar via API: {err}", file=sys.stderr)
        print(f"[*] Fallback — URL direta da fila com os vídeos:\n    {queue_url}", file=sys.stderr)
        if args.open:
            subprocess.Popen(["xdg-open", queue_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.exit(1)


if __name__ == "__main__":
    main()
