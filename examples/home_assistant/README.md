# Home Assistant integration (example)

A [Home Assistant](https://www.home-assistant.io/) custom integration built on this library:
your public library account as sensors. It only reads: nothing is borrowed, renewed or
reserved.

## Sensors

One device per login, with these sensors:

| Sensor | State | Attributes |
| --- | --- | --- |
| Loans | number of loans, digital and physical | `loans`: title, author, type, due, renewable |
| Next due | when the next loan is due (timestamp) | `title` |
| Overdue loans | loans past their due date | `loans` |
| Reservations | number of reservations | `reservations`: title, author, type, state, queue position, pickup branch, pickup by |
| Ready for pickup | reservations waiting for you | `reservations` |
| E-book loans left | e-book loans left this period | `per_period` |
| Audiobook loans left | audiobook loans left this period | `per_period` |
| Fees | sum of fees, in DKK | `fees`: reason, amount, due |

The lists hold at most 25 items each. The account is polled every 30 minutes.

Example automation: a notification when a reservation is ready.

```yaml
automation:
  - alias: Library book ready
    triggers:
      - trigger: state
        entity_id: sensor.vejle_bibliotekerne_ready_for_pickup
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.state | int(0) > trigger.from_state.state | int(0) }}"
    actions:
      - action: notify.notify
        data:
          message: >
            Ready for pickup:
            {{ state_attr(trigger.entity_id, 'reservations') | map(attribute='title') | join(', ') }}
```

## Install

Copy `custom_components/biblo` into your Home Assistant config's `custom_components`
folder and restart. Then go to **Settings > Devices & services > Add integration >
Biblo**, pick your library and log in with card number/CPR and pincode, the same login as
the Biblo app. If the library later refuses the login (a new pincode), Home Assistant asks
for the pincode again.

The manifest requires `biblo==0.2.0`, which Home Assistant installs. The integration needs
a Home Assistant release whose config flows use `probatio`; it is tested on 2026.10.4.

What it keeps:

- The login is stored in the config entry, in Home Assistant's `.storage` like other
  integrations' credentials. The entry's unique id is a hash, not the card number or CPR.
- Tokens are kept only in memory: after a restart it logs in again.
- Requests to the API say they come from `home-assistant-biblo` in their User-Agent.

## Tests

The tests run the integration against the fake Biblo API and login.bib.dk in this repo's
`tests/fake.py`, using Home Assistant's own test harness:

```sh
uv venv -p 3.14 .ha-venv
VIRTUAL_ENV=.ha-venv uv pip install pytest-homeassistant-custom-component biblo==0.2.0
.ha-venv/bin/python -m pytest examples/home_assistant/tests -o addopts=""
```

They cover the config flow (login, wrong pincode, the same login twice), the sensors,
overdue loans and reservations ready for pickup, logging in again, the API being down,
and unloading. It hasn't been run against a real account yet.
