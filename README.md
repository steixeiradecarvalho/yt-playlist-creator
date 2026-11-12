# YouTube Playlist Creator (`.txt` / CLI / GUI)

Assistente e utilitário em Python para criar playlists no YouTube a partir de arquivos `.txt` (simples ou estruturados com blocos e comentários), arquivos `.json` ou diretamente pela linha de comando.

Realiza a busca automática dos vídeos via [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) e permite:
- **Criar a playlist diretamente na sua conta do YouTube** via **OAuth 2.0 (YouTube Data API v3)** já com título, descrição completa e visibilidade (`public`, `unlisted` ou `private`).
- **Gerar e abrir uma fila rápida no navegador** (`watch_videos?video_ids=...`) sem necessidade de credenciais de API.
- **Usar modo interativo no terminal** passo a passo ou **Interface Gráfica (Tkinter)**.

---

## Instalação

```bash
git clone https://github.com/steixeiradecarvalho/yt-playlist-creator.git
cd yt-playlist-creator

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

*(Opcional)* Criar atalhos globais em `~/.local/bin`:

```bash
chmod +x yt_playlist.py
ln -sf "$(pwd)/yt_playlist.py" ~/.local/bin/yt-playlist
```

---

## Formatos de Arquivo `.txt` Aceitos

O parser (`parse_playlist_txt`) reconhece tanto listas simples (uma música por linha) quanto roteiros comentados:

```text
Playlist: Sinfonia das Guitarras Gêmeas (O Diálogo dos Instrumentos)
Privacidade: public

Bloco 1: O Berço e a Crueza de Nova York
• The Velvet Underground – What Goes On
	• O que observar: O ritmo frenético de Sterling Morrison servindo de tapete para as experimentações cortantes de Lou Reed.
• The Velvet Underground – Beginning to See the Light
	• O que observar: A levada descontraída e balançada que antecipa diretamente o clima de "Rock 'N' Roll".
• Richard Hell & The Voidoids – Blank Generation
	• O que observar: O duelo áspero e a atitude provocativa entre as guitarras de Robert Quine e Ivan Julian.
```

- **Título**: extraído automaticamente de `Playlist: ...`, `Título: ...` ou `# ...` (ou do nome do arquivo).
- **Descrição**: preserva os blocos (`Bloco 1:...`) e observações (`• O que observar:...`).
- **Faixas**: isola apenas os nomes das músicas para busca precisa no YouTube.

---

## Como Usar

### 1. Assistente Passo a Passo no Terminal
```bash
./yt_playlist.py
# ou passando um arquivo .txt:
./yt_playlist.py sinfonia_guitarras_gemeas.txt
```

### 2. Interface Gráfica (Tkinter)
```bash
./yt_playlist.py --gui
# ou carregando um arquivo .txt:
./yt_playlist.py --gui sinfonia_guitarras_gemeas.txt
```

### 3. Linha de Comando Direta (Automação)
```bash
# Criar na conta via OAuth a partir de um .txt e abrir no navegador:
./yt_playlist.py --from-txt sinfonia_guitarras_gemeas.txt --open

# Gerar apenas a fila rápida no navegador (sem usar a API):
./yt_playlist.py --from-txt sinfonia_guitarras_gemeas.txt --queue-only --open

# Passando faixas avulsas diretamente:
./yt_playlist.py -t "Pós-Punk & New Wave" -p public --open \
  "Television - Marquee Moon" \
  "The Strokes - Reptilia"
```

---

## Configuração do OAuth 2.0 (YouTube Data API v3)

Para que o app crie as playlists diretamente na sua conta do YouTube:

1. No [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com), ative a **YouTube Data API v3**.
2. Em [Credenciais](https://console.cloud.google.com/apis/credentials), clique em **+ Criar Credenciais** $\rightarrow$ **ID do cliente OAuth** $\rightarrow$ tipo **App para computador** (*Desktop app*).
3. Se o aplicativo estiver em modo de **Teste**, acesse **Google Auth Platform $\rightarrow$ Público-alvo (Audience)** e adicione seu e-mail em **Usuários de teste** (*Test users*).
4. Baixe o JSON da credencial e salve-o na pasta do projeto como:
   ```text
   client_secret.json
   ```
5. Execute `./yt_playlist.py --auth-only` na primeira vez para autorizar no navegador e gerar o `token.json` local.
