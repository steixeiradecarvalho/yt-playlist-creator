### Como usar o App

  #### Opção 1: Assistente Passo a Passo no Terminal

  Basta rodar no terminal (sem argumentos, ou passando o seu arquivo .txt):

    yt-playlist
  ou apontando direto para um arquivo .txt:
    yt-playlist ~/playlist/sinfonia_guitarras_gemeas.txt

  O que o assistente faz para orientar você:

  1. Seleção do .txt: Lista automaticamente todos os arquivos .txt da pasta atual e de ~/playlist (ou cria um arquivo .txt modelo na hora se você escolher
  [N]).
  2. Leitura Inteligente: Extrai o título (Playlist: ...), separa os blocos/comentários (Bloco ..., O que observar: ...) para a descrição da playlist e isola
  apenas as músicas.
  3. Busca e Revisão das Faixas: Pesquisa cada música no YouTube, mostra o título do vídeo, canal e link encontrado, e pergunta se você quer substituir alguma
  faixa (basta digitar o número da faixa para buscar outra versão ou colar um link).
  4. Criação Guiada: Permite escolher entre:
      • [1] Abrir a fila pronta no navegador imediatamente + exibir Título e Descrição prontos para copiar/colar.
      • [2] Criar direto na sua conta via OAuth (e, caso ainda não tenha o client_secret.json, mostra o passo a passo e oferece abrir o Console do Google
      Cloud).
      • [3] Salvar também um .json da playlist e abrir no navegador.

  ──────
  #### Opção 2: Interface Gráfica (Janela)

  Se preferir usar uma janela visual com botões para selecionar o .txt, editar título/descrição/faixas e clicar para buscar e criar:

    yt-playlist-gui

  (ou yt-playlist-gui ~/playlist/sinfonia_guitarras_gemeas.txt)
  ──────
  #### Opção 3: Modo Direto (Sem Perguntas)

  Para criar ou abrir a fila direto de um .txt em um único comando:

    # Abrir direto a fila no navegador a partir de um .txt:
    yt-playlist --from-txt ~/playlist/sinfonia_guitarras_gemeas.txt --queue-only --open

    # Criar direto na conta via OAuth a partir de um .txt:
    yt-playlist --from-txt ~/playlist/sinfonia_guitarras_gemeas.txt --open
  ──────
  ### Formatos de .txt aceitos

  O parser (parse_playlist_txt) aceita tanto uma lista simples (uma música por linha) quanto textos estruturados como o que você enviou:

    Playlist: Nome da Playlist
    Privacidade: public

    Bloco 1: Nome do Bloco (vai para a descrição)
    • Artista – Nome da Música
        • O que observar: Comentário sobre a faixa (vai para a descrição)
    • Outro Artista - Outra Música

