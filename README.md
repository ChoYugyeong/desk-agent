# desk-agent

MCP나 API가 없는 윈도우 exe를 AI가 **사용자 마우스와 화면을 뺏지 않고** 읽고 조종하게 하는 도구. agent-browser처럼 줄인 트리와 `@e12` ref로 동작한다.

- 진짜 마우스·키보드 입력(SendInput, SetCursorPos)은 코드에 없다. 창 메시지, 앱 안의 웹 화면(IE) DOM, 접근성 API(MSAA, UI Automation)로만 조작한다. 동작할 때마다 커서 위치를 비교해서, 움직였으면 표시한다.
- **관리자 권한으로 도는 앱도 조작한다.** 일반 권한 프로세스는 관리자 앱을 읽을 수는 있어도 조작은 막힌다(UIPI). 그래서 에이전트가 관리자 권한으로 한 번 뜨고(UAC 1회), 일반 권한 `desk` CLI와 MCP 서버가 127.0.0.1 + 토큰으로 명령을 보낸다.
- 에이전트는 **프로필**에 적은 프로세스의 창만 다룬다.

## 설치

```
powershell -ExecutionPolicy Bypass -File install.ps1          # venv, 의존성(comtypes 하나), 자체 점검
powershell -ExecutionPolicy Bypass -File install.ps1 -Test    # + 자동 테스트
powershell -ExecutionPolicy Bypass -File install.ps1 -Skill   # + ~/.claude/skills에 스킬 복사
powershell -ExecutionPolicy Bypass -File install.ps1 -Mcp     # + Claude Code에 MCP 등록 (user 범위)
```

Python 3.10 이상이 필요하다.

## 사용법

```
desk start --profile notepad                 # 관리자 에이전트 시작 (UAC 1회). --allow a.exe,b.exe로 직접 지정도 가능
desk windows                                 # 대상 창 목록 [w1] [w2] ...
desk notes                                   # 프로필에 적어 둔 앱 메모(내부 이름, 주의할 점)
desk find "저장" [--role button]              # 맞는 요소만 (전체 트리 대신)
desk snapshot [-w w1] [-i] [--root e170]     # 줄인 트리 + ref. -i: ref 있는 줄만, --root: 그 요소 아래만
desk snapshot -w w1 --diff                   # 직전과 바뀐 줄만
desk click e13 [--diff]                      # 클릭. --diff: 창이 멈출 때까지 기다렸다가 바뀐 줄을 같이 출력
desk click w1 --at 125,37                    # `desk shot w1` 이미지의 픽셀 좌표 그대로 클릭
desk type e5 "텍스트" [--diff]                # 입력칸 / select(옵션 글자나 value)
desk key e5 enter [--diff]
desk wait --text "완료" | --gone T | --title T | --stable [--timeout 10]
desk read e30                                # 글자, 표 행(탭 구분), 입력값
desk shot w1 -o out.png                      # 그 창만 캡처 (가려져 있어도 됨, 최소화는 안 됨)
desk acc w1 / desk uia w1                    # MSAA / UI Automation 트리 전체
desk js -w w1 "document.title" --confirm     # 창 안의 IE 페이지에서 식 계산
desk doctor                                  # 진단: 권한, 프로필, 대상 앱, 최소화된 창, 정책, 최근 오류
desk mcp                                     # MCP 서버로 실행 (stdio)
desk skill                                   # AI용 사용 설명서 출력
desk reload                                  # 에이전트 코드 수정 반영 (UAC 없이)
desk stop
```

`desk.cmd`가 프로젝트 venv의 python으로 `desk.py`를 실행한다. PowerShell에서는 ref를 `e12`나 `'@e12'`로 쓴다(맨 `@e12`는 PowerShell splatting).

## 읽는 방법 (레이어)

| 대상 | 방법 | 조작 |
|---|---|---|
| Win32/WinForms 컨트롤 (버튼, 입력칸, 체크박스, 라벨) | 창 핸들 열거 + WM_GETTEXT. 체크박스·라디오는 MSAA 역할로 구분 | BM_CLICK, WM_SETTEXT, PostMessage 키 |
| IE WebBrowser 컨트롤 (`Internet Explorer_Server`) | WM_HTML_GETOBJECT → DOM, 페이지 안에서 ES3 스크립트 1회 실행 | `el.click()`, 값 설정 + change/keyup 이벤트, 프레임 포함 |
| 직접 그리는 컨트롤 (리본, 툴바, 표: C1·DataGridView 등) | MSAA(IAccessible). 표는 행 하나를 한 줄로, 행의 accValue 한 번에 | accDoDefaultAction, accValue 설정 |
| WPF, WinUI/XAML, UWP (자식 창 핸들도 MSAA도 없는 앱) | UI Automation. 하위 트리를 캐시 요청 한 번에 가져온다 | Invoke, Toggle, SelectionItem, ExpandCollapse, Value 패턴 |
| 그래도 안 보이는 영역 | `shot`으로 보고 `click w1 --at x,y` | 마우스 메시지 PostMessage (진짜 커서 아님) |
| CEF(내장 크롬) | 접근성이 꺼져 있으면 못 읽음 | 사람이 한다 |

클릭으로 모달 창이 떠도 에이전트가 멈추지 않게 클릭은 비동기로 보낸다. 모달 창은 다음 스냅샷에 `[wN] dialog`로 나온다.

## 프로필

```
profiles/NAME.json          예시(공개). 예: notepad
local/profiles/NAME.json    내 앱, 회사 전용 규칙과 메모 (git에 안 올라감, 같은 이름이면 이쪽이 우선)
local/config.json           {"default_profile": "NAME"}: desk start의 기본 프로필
```

```json
{
  "allow": ["notepad.exe"],
  "confirm_labels": ["앱 전용 위험 단어"],
  "confirm_keys": ["f10"],
  "notes": "notes/notepad.md"
}
```

`notes`에는 그 앱의 내부 컨트롤 이름, 화면 구성, 하면 안 되는 동작을 적는다. AI는 `desk notes`(MCP `desk_notes`)로 먼저 읽는다.

## 동작 정책 (policy.json)

밖으로 나가는 동작(발송, 전송, 삭제, 결제, 송금 등)을 실수로 누르지 않도록 관리자 에이전트 안에서 검사한다. CLI든 MCP든 스크립트든 같은 규칙을 거친다.

- `confirm_labels`: 대상의 글자나 접근성 이름에 이 단어가 들어 있으면(대소문자 무시) `--confirm` 없이는 거부한다(`POLICY_DENIED`). 프로필의 단어가 더해진다.
- `confirm_keys`: 확인이 필요한 키. 버튼에 enter/space를 보내는 것도 클릭으로 보고 검사한다.
- `confirm_js`: 페이지 스크립트(`desk js`)는 무엇이든 누를 수 있으므로 확인이 필요하다.
- `confirm_at_clicks`: 좌표 클릭은 그 자리의 실제 자식 컨트롤 글자로만 검사된다. 전부 확인받으려면 `true`.
- 창 전체나 직접 그리는 영역을 `--at` 없이 클릭하면 거부한다(가운데를 누르지 않는다).
- 기계마다 다르게 두려면 `%LOCALAPPDATA%\desk-agent\policy.json`에 복사해서 고친다. 파일이 깨져 있으면 정책을 끄지 않고 오류를 낸다.

## 오류 코드

첫 줄이 `코드 retry=safe|unsafe|never: 내용`, 둘째 줄이 `hint: 할 일`이다. AI는 문구가 아니라 코드로 판단하고, `retry=safe`일 때만 자동으로 다시 시도한다.

`UNKNOWN_REF`, `STALE_REF`(다시 스냅샷), `WINDOW_NOT_FOUND`, `WINDOW_MINIMIZED`, `POLICY_DENIED`, `TIMEOUT`, `ACTION_NOT_SUPPORTED`, `INVALID_ARGS`, `APP_ERROR`·`INTERNAL`(retry=unsafe: 적용됐는지 먼저 확인), `NOT_RUNNING`(MCP).

## MCP

도구: `desk_snapshot`, `desk_find`, `desk_wait`, `desk_click`, `desk_type`, `desk_key`, `desk_read`, `desk_shot`(이미지), `desk_acc`, `desk_windows`, `desk_notes`, `desk_doctor`. 의존성 없이 직접 구현했다(stdio, JSON-RPC). 읽기 도구에는 `readOnlyHint`, 조작 도구에는 `destructiveHint`를 붙였다.

```
claude mcp add desk -- <repo>\.venv\Scripts\python.exe <repo>\desk.py mcp
```

AI용 사용법은 [skills/desk-agent/SKILL.md](skills/desk-agent/SKILL.md)에 있다.

## 자원 사용

- 명령이 20초 동안 없으면 백그라운드 모드(낮은 CPU·디스크·메모리 우선순위)로 내려가고 작업 메모리를 페이지 파일로 내보낸다. 실측으로는 22.7MB에서 2.6MB로 줄었다. 다음 명령이 오면 보통 모드로 돌아온다.
- `--diff`용 직전 스냅샷은 `%LOCALAPPDATA%\desk-agent\snapshots\`에 파일로 둔다. ref는 3000개까지만 기억한다.

## 테스트

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

WinForms 시험 창(win32 컨트롤, 체크박스, 정책, MSAA 표)과 WPF 시험 창(UI Automation)을 띄워서 스냅샷, 클릭 + diff, 입력, find, wait, 정책 거부와 확인, 오류 코드를 확인한다. 시험 창은 최소화된 채로 떠서 포커스를 가져가지 않고, 끝나면 닫힌다. 테스트는 에이전트를 같은 프로세스에서 돌리므로 UAC도 실행 중인 에이전트도 필요 없다. `DESK_TEST_NOTEPAD=1`이면 메모장 읽기 시험도 한다(메모장이 이미 켜져 있으면 건너뜀).

## 다음 과제

- 작업 스케줄러에 "가장 높은 권한"으로 등록해서 UAC 없이 시작하기 (시스템에 남는 설정이라 선택 사항)
- 감사 기록: 실행한 동작과 정책 거부를 따로 남기고 `desk log`로 보기
- CEF(내장 크롬) 화면 읽기
