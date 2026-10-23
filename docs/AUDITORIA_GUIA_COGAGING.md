# Auditoria do "Guia para processamento das RME — Cog-Aging" e sua réplica no pipeline

Data: 23/10/2026. Fonte auditada: página do Notion "Guia para processamento das
RME — Cog-Aging" (seções 1 a 8). Sem dados de paciente neste documento.

## Resumo

O guia é tecnicamente bom: os comandos do FreeSurfer, SAMSEG, FSL e
fMRIPrep/XCP-D foram validados em sujeitos reais (GER00039, GER00158), com
tempos reais e lições aprendidas corretas (SAMSEG só no arm64, fMRIPrep não
reaproveita recon-all de outra versão, TRACULA ausente no FreeSurfer 8.2 arm64,
asterisco do "DWI 64-ADNI3*" funcionando como curinga). Os problemas estão na
consistência entre máquinas e etapas, na privacidade dos dados e no formato
"copiar e colar", que é a maior fonte de erro para os ICs.

As etapas iniciais (conversão, recon-all e SAMSEG) agora rodam pelo pipeline
compartilhado (`projects/COGAGING`, menu `Pipeline_Alunos/iniciar.command`),
com os mesmos parâmetros do guia.

## Achados, por gravidade

| # | Achado | Seção | Impacto | O que o pipeline faz agora |
|---|---|---|---|---|
| 1 | Duas versões de FreeSurfer na mesma coorte: 8.2.0 no Mac Mini e 7.4.1 (Rosetta) no gikamoro; o recon do QSIPrep foi validado na 7.4.1 | 3, 8 | Volumes e espessura mudam entre versões: confundidor dentro da coorte | `run_recon_long.py` grava a versão em cada saída e se recusa a misturar versões no mesmo SUBJECTS_DIR (`--allow-mixed-versions` para exceções conscientes). **Decisão do laboratório:** uma versão para os 74 |
| 2 | Até três recon-all por sujeito (`-s GER00039`, `-s sub-GER00158` e o do fMRIPrep) | 3, 5, 8 | ~4–5 h de máquina a mais por sujeito e saídas diferentes para o mesmo cérebro | Um recon por sessão, em `bids/derivatives/freesurfer/sub-GER…_ses-N`. O do fMRIPrep continua à parte (lição do OMP Error #13) |
| 3 | Saídas dentro da pasta de instalação (`/Applications/freesurfer/8.2.0/subjects`), com `sudo chmod -R a+w` | 3.4, 4 | Atualizar/reinstalar o FreeSurfer apaga resultados; exige permissão de administrador | Tudo dentro de `projects/COGAGING/bids/derivatives/` |
| 4 | Sidecars não desidentificados: o dcm2bids mantém instituição, endereço, estação, número de série e StudyDescription (confirmado no GER00007) | 2 | Nome do hospital em todo arquivo `.json` | Conversão com `dcm2niix -ba y` + `deidentify_sidecars.py` (inclui StudyDescription); importação do BIDS antigo remove os mesmos campos |
| 5 | Sessões com a data do exame (`ses-202502`) | 2.11 | A data do exame é identificador e vai para todo nome de arquivo | Sessões `ses-1`, `ses-2`… pela ordem das datas; o mapa data↔número fica em `sourcedata/session_map.tsv`, fora do `bids/`. Exame novo mais antigo que um já numerado para a execução |
| 6 | Mudar o limiar do SAMSEG "sem refazer" (binarizando os posteriors) altera a máscara mas não o volume em `samseg.stats` | 4.5 | Número do relatório diferente da máscara vista no QC | `run_samseg.py --threshold X` refaz a sessão; a tabela registra o limiar de cada linha |
| 7 | Relatório individual com valores inventados: `mean_fd = 0.186` quando falta o QC, censurados fixos em 22, TR/volumes fixos (o GER00007 tem 197 volumes, não 200) | 6 | Números que não são do paciente | Pendente (porte do relatório para `code/`, depois dos arquivos do FreeSurfer) |
| 8 | Fluxo de copiar e colar: variáveis que somem a cada Terminal, `DCM` vazio (a "lição nº 1" do próprio guia), script em `~/Downloads`, caminhos diferentes por máquina | todas | Principal fonte de erro dos ICs | Menu guiado; cada passo encontra os arquivos sozinho, pula o que já foi feito e para com mensagem clara |
| 9 | Pontos a conferir: `xtract_stats -warp` (a documentação usa `-w`); flag `-3T` no FreeSurfer 8 | 7.3, 3.2 | Pode falhar ou ser ignorado em silêncio | Mantido `-3T` (igual ao guia); conferir no primeiro log do Mac Mini |
| 10 | Nenhuma etapa de remoção facial | — | Necessária antes de qualquer compartilhamento | Fora do COGAGING até o estudo decidir sobre compartilhamento externo |
| 11 | Conversão por série dentro de um CD clínico: reformatações (MPR, "CORONAL CRANIO") e T1 pós-contraste podem ser tomados como "o T1" | 2.9 | recon-all no T1 errado | Imagem com ImageType DERIVED vai para `derivatives/`; T1 pós-contraste recebe `ce-gad` e nunca entra no recon; com mais de um T1, vence o 3D |

## Do guia para o pipeline

| Guia | Pipeline (`--project COGAGING`) | Situação |
|---|---|---|
| 2. dcm2bids + `dcm2bids_draft.py` | Passos 1–4: `dicom_to_bids.py` → `deidentify_sidecars.py` → `make_bids.py` → `make_derivatives.py`. Regras da seção 2.9 traduzidas em `projects/COGAGING/series_map.csv` | Pronto. Testado com CDs sintéticos (Siemens); falta validar com CDs reais Philips/GE |
| Sujeitos já convertidos | Item I do menu: `import_bids.py` | Pronto (GER00007 importado) |
| 3. recon-all `-all -qcache -3T` | Passo 6: `run_recon_long.py` (mesmos parâmetros, só T1) | Pronto; ainda não rodado no Mac Mini |
| 4. SAMSEG | Passo 8: `run_samseg.py` (mri_coreg → run_samseg `--lesion` → máscara → tabela) | Pronto; ainda não rodado no Mac Mini |
| 5. fMRIPrep + XCP-D | — | Próxima etapa |
| 6. Relatório individual | — | Próxima etapa (corrigir os itens do achado 7) |
| 7. DTI + XTRACT | — | Próxima etapa (a DWI de 64 direções já sai da conversão) |
| 8. QSIPrep + QSIRecon | — | Próxima etapa |

## O que conferir na primeira rodada no Mac Mini

1. `python3 code/check_environment.py --project COGAGING` e carregar o FreeSurfer (`freesurfer`).
2. Passos 1–4 num CD real, conferindo `bids/sourcedata/conversion/reports/unknown_series_report.csv`: cada série desconhecida vira uma linha nova em `projects/COGAGING/series_map.csv`.
3. Passo 6 em um sujeito, conferindo no log se o `-3T` foi aceito pelo FreeSurfer 8.2.
4. Passo 8 no mesmo sujeito e QC visual da máscara.
5. Comparar volumes com o GER00039 já processado pelo guia (mesma versão do FreeSurfer).

## Decisões em aberto

- Versão única de FreeSurfer para a coorte (achado 1).
- Reprocessar pelo pipeline os sujeitos já rodados pelo guia, para que todos tenham as mesmas pastas e a mesma versão.
- Remoção facial e compartilhamento externo (achado 10).
- Pedir à radiologia o par AP/PA para DWI e BOLD nas próximas coletas (o guia já recomenda).
