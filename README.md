# Administrationsservice Fyn for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate with hassfest](https://github.com/LovelessCodes/administrationsservice_fyn/actions/workflows/hassfest.yml/badge.svg)](https://github.com/LovelessCodes/administrationsservice_fyn/actions/workflows/hassfest.yml)
[![Lint](https://github.com/LovelessCodes/administrationsservice_fyn/actions/workflows/lint.yml/badge.svg)](https://github.com/LovelessCodes/administrationsservice_fyn/actions/workflows/lint.yml)

A custom Home Assistant integration for **Administrationsservice Fyn** (ASFyn).
It reads electricity, district heating, hot water, water and gas meters from
the ASFyn self-service portal and exposes them as long-term statistics friendly
sensors.

> This is an unofficial integration. It is not affiliated with or supported by
> Administrationsservice Fyn or Energi Fyn.

## Features

- Electricity consumption in **kWh**
- District heating in **GJ**
- Hot water in **m³** (when the heating meter reports a water volume)
- Water consumption in **m³**
- Gas consumption in **m³** (when present on the account)
- One Home Assistant device per property/estate
- Automatic OAuth refresh-token rotation
- Lifetime, month-to-date and year-to-date values as attributes
- Long-term statistics with hourly electricity and daily water/heating values
- Reauthentication flow when the login expires
- Configurable polling interval (15 minutes to 24 hours)
- Manual refresh button
- Diagnostics download with tokens redacted

Electricity spot prices are **not** included. This integration focuses on
metered consumption; keep a price integration if you need hourly prices.

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/LovelessCodes/administrationsservice_fyn` with
   category **Integration**.
4. Search for **Administrationsservice Fyn**, install it, and restart Home
   Assistant.
5. Go to **Settings → Devices & services → Add integration** and search for
   **Administrationsservice Fyn**.

### Manual

Copy `custom_components/administrationsservice_fyn` into your Home Assistant
`config/custom_components` directory and restart Home Assistant.

## Setup

The integration uses the same login as the ASFyn self-service portal. Home
Assistant needs a **refresh token** from that login; it then obtains and renews
its own short-lived access tokens.

1. Open <https://www.asfyn.dk/> and log in with MitID.
2. Open your browser's developer tools:
   - **Chrome / Edge:** `F12` → **Application**
   - **Firefox:** `F12` → **Storage**
   - **Safari:** **Develop** → **Show Web Inspector** → **Storage**
3. Open **Session Storage**, select
   `oidc.user:https://accounts.forsyningslogin.dk:af-spa`, and copy the
   `refresh_token` field from the JSON value.
4. Alternatively, open **Cookies** for `accounts.forsyningslogin.dk` (or
   `www.asfyn.dk`) and copy the **`sso_refresh_token`** value. It is often
   marked `HttpOnly`; that is fine, the value is still visible in the developer
   tools.
5. Paste the value into the integration setup form.

The value may be URL-encoded. The integration decodes it automatically, so
paste it exactly as shown.

### About the refresh token

- The refresh token is rotated by the SSO every time it is used. The
  integration saves the new token automatically after each refresh.
- Do not paste the same refresh token into more than one Home Assistant
  instance. Each use rotates the token, and the other instance will be logged
  out.
- If you sign out of the portal, revoke the session, or the token otherwise
  becomes invalid, Home Assistant will show a **Reauthenticate** notification.
  Log in again and paste a fresh `sso_refresh_token`.

## Sensors

Sensors are created per property and per active product. Their names are
translated (English and Danish).

Sensors come from the customer profiles linked to your ASFyn login.
Electricity, water, district heating and gas are separate customer profiles.
If you only see Electricity, the other profiles are not linked yet.

| Product | Sensor | Unit | Home Assistant device class |
| --- | --- | --- | --- |
| `el` | Electricity | kWh | `energy` |
| `heat` | Heating | GJ | `energy` |
| `heat` | Hot water | m³ | `water` |
| `water` | Water | m³ | `water` |
| `gas` | Gas | m³ | `gas` |

The **Hot water** sensor is created when the heating product detail contains
a water-volume reading (`latestReadingsM3`). Some district heating meters only
report energy in GJ.

The integration fetches the product detail for every meter and builds a
cumulative consumption value from the API's data layers:

- the lifetime total through the latest physical meter reading,
- the current year-to-date,
- the current month-to-date (the API updates this daily, with a one-day lag).

The lifetime total's cutoff is the latest physical reading, not always the end
of the previous year:

- If the latest reading was in a **previous year** (typical for an annual
  district-heating meter), the state is
  `lifetime + year-to-date + month-to-date`.
- If the latest reading is in the **current year** (typical for a monthly
  water meter), the lifetime already includes the year up to that reading.
  The integration adds every full month after the reading, including the
  current month. This keeps the value current without adding year-to-date
  twice.
- Electricity has no physical reading date, so it uses the
  `base + year-to-date + month-to-date` pattern.

The physical meter reading is exposed as the `meter_reading` attribute; it may
only change monthly or yearly, while the consumption value updates much more
often. The raw reading can include the meter's initial offset, so it is not
the same as consumption since installation.

All sensors use `state_class: total_increasing` and are suitable for the Home
Assistant **Energy** and **Water** dashboards.

### Water and district heating

ASFyn does not discover utilities from the property address. It reads the
customer numbers linked to your login.

Water billed by **Vandcenter Syd** is a separate portal
(`vandcenter.smartforsyning.dk`) and is **not** part of ASFyn. Keep the
Vandcenter Syd integration for that meter; ASFyn cannot expose it.

If you have both integrations, they can refer to the same physical Axioma W1
meter. The Vandcenter Syd sensor is the real-time meter reading. The ASFyn
water product is the utility/billing view and can lag until the next monthly
reading is posted; the integration fills in the full months that the API
already exposes, but a month that the utility has not published yet cannot be
recovered. Use the Vandcenter Syd sensor for the **Water** dashboard when it
is available.

To add a water or district heating account that *is* handled by ASFyn:

1. Log in at <https://www.asfyn.dk/>.
2. Open **My EnergiFyn** → **Change customer** (the portal also exposes
   `/select-customer/`).
3. Choose **Add customer number**, then enter the customer number and PIN code
   for the water/heating account. You can add several at once.
4. In Home Assistant, open **Settings → Devices & Services →
   Administrationsservice Fyn** and choose **Reload** (or restart Home
   Assistant). New sensors are created during setup, not on the next poll.

If you do not know the customer number or PIN, check the bill or self-service
portal for Vandcenter Syd, Fjernvarme Fyn or the relevant utility. The ASFyn
portal also has a customer-number lookup by address.

### Energy dashboard

- **Electricity** → *Grid consumption*.
- **Water** → *Water consumption*.
- **Gas** → *Gas consumption*.

Home Assistant 2026.10 has no native **district heating** energy source. A
district heating meter can still be tracked by adding it as a **Gas** source
and giving it a custom name such as *District heating*. The Gas dialog accepts
energy sensors in GJ, so the value and unit are preserved.

The **Hot water** sensor is the district heating circuit volume (m³), not
domestic hot water. Do not add it to *Water consumption* alongside the cold
water meter; that would double-count water. Use it in a statistics card or
automation instead.

### Long-term statistics

ASFyn returns a **day** series for electricity and a **month** series with one
value per day for water, heating, hot water and gas. The integration imports
those series into Home Assistant long-term statistics, independently of the
sensor state:

| Sensor | Statistic resolution | Backfill on first setup |
| --- | --- | --- |
| Electricity | Hourly | 7 days |
| Water | Daily | 90 days |
| District heating | Daily | 90 days |
| Hot water | Daily | 90 days |
| Gas | Daily | 90 days |

The statistic IDs are stable and include the customer, estate, installation,
meter and sensor, for example:

```text
administrationsservice_fyn:12345678_1234567_123456_1_electricity_consumption
```

The current statistic ID is also exposed as the `statistic_id` attribute on
each sensor. The statistics are **external statistics**, not entities. They do
not change the sensor states and cannot be used directly as automation
triggers.

On every refresh the integration re-imports the most recent days, so late or
corrected meter values replace the earlier rows. The first setup backfills the
periods in the table above. ASFyn publishes a day with a delay and returns
HTTP 500 for a day that is not available yet, so the hourly import scans the
whole window and imports every day that is already published. Yesterday may
therefore be missing until ASFyn publishes it; the next refresh fills it in.
The hourly electricity date parameters are discovered automatically.

To use these series in the **Energy dashboard**, replace the existing source
with the matching statistic rather than adding both. Adding both the source
entity and its external statistic would count the same consumption twice. The
entity sensors remain the right choice for automations and for the current
meter value.

### Attributes

Each sensor exposes useful metadata:

| Attribute | Description |
| --- | --- |
| `customer_number` | ASFyn customer number |
| `estate_id` | Property/estate identifier |
| `estate_name` | Property address |
| `installation_id` | Meter installation identifier |
| `meter_id` | Meter identifier used by the API |
| `meter_number` | Meter number |
| `customer_meter_number` | Meter number shown to the customer |
| `product_name` | Product name, for example `SpotEl` |
| `product_type` | `el`, `heat`, `water` or `gas` |
| `tariff` | Tariff code, for example `EK-30` |
| `subscription_state` | Product subscription state |
| `last_reading` | Date of the latest physical meter reading |
| `meter_reading` | Latest physical meter reading |
| `lifetime_consumption` | Lifetime consumption through the latest physical reading |
| `month_to_date` | Consumption for the current month |
| `year_to_date` | Consumption for the current year |
| `added_months` | Month totals added after the latest physical reading |
| `value_source` | `cumulative_total` or `meter_reading` |
| `statistic_id` | External long-term statistic for this sensor |

The sensor state is cumulative consumption. The physical meter reading is the
`meter_reading` attribute. This means the sensor can update daily even when
the utility only publishes a new physical meter reading once a month.

## Options

Open the integration's **Configure** dialog to change the polling interval.
The default is 60 minutes. Meter readings usually change only a few times per
day, so polling more often than every 15 minutes is not recommended.

## Data updates

The integration polls the ASFyn self-service API. The API is not a real-time
feed: values are updated when the utility receives new meter data. The
current-month endpoint is updated daily (with a one-day lag), the year endpoint
monthly, and the physical meter reading monthly or yearly. A manual **Refresh**
button is available on the integration device.

The long-term statistics import runs in the background after a refresh. It
stores electricity hour by hour and the other products day by day, so the
statistics show when consumption happened instead of when ASFyn published the
reading. The live sensor state remains the cumulative meter value. The first
backfill can take a few minutes; Diagnostics shows the point count and last
import time for each statistic once it finishes.

New meters are discovered when the integration starts. If a new product is
added to your account, reload the integration or restart Home Assistant to
create its sensors.

## Troubleshooting

### "The refresh token is invalid or has expired"

The token was already used, rotated, or revoked. Log in to the portal again and
paste a fresh `sso_refresh_token`.

### "No active meters were found"

The login succeeded, but the account has no active electricity, heating, water
or gas products. Check the products in the portal. If a product was just
created, wait until it becomes active.

### Only Electricity is shown

Water, district heating and gas are separate customer profiles. Link those
customer numbers in the ASFyn portal, then reload the integration. See
[Water and district heating](#water-and-district-heating).

### Statistics are missing or empty

The statistics import needs the Home Assistant **Recorder** integration, which
is enabled by default. Download **Diagnostics** from the integration and check
the `statistics` section for each sensor's statistic ID, imported point count
and last import time. If the section is empty, wait for the background import
to finish, then check again. If it stays empty, the product did not expose a
`day` or `month` series, or the API returned no items. The `day_param_kind` and
`hourly_probe` fields show which query format worked and which day was probed.

The external statistics start with the backfill window; they do not contain
all history from the entity sensor. Importing the same consumption into both
the entity and the external statistic would double-count it in the Energy
dashboard.

### "Could not reach the Administrationsservice Fyn API"

Home Assistant could not reach the API. Check DNS, IPv6/IPv4 connectivity and
any firewall or Pi-hole rules for:

- `efselfserviceapi.azurewebsites.net`
- `efprofileservice.azurewebsites.net`
- `accounts.forsyningslogin.dk`

### Debug logging

```yaml
logger:
  logs:
    custom_components.administrationsservice_fyn: debug
```

## Privacy

The refresh token is stored in the Home Assistant config entry and is only sent
to `accounts.forsyningslogin.dk`. Access tokens are sent only to the ASFyn API
hosts listed above. Tokens are redacted from diagnostics downloads.

## License

MIT — see [LICENSE](LICENSE).
