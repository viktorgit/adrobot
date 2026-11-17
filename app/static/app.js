/* No framework or build step. All remote text is escaped before rendering. */
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let campaign = null;
let page = 0;
let query = "";
let busy = false;
let routeVersion = 0;
let toastTimer;
let offerTarget = null;
let offerPage = 0;
let offerQuery = "";
let offerSearchVersion = 0;

function toast(message, error = false) {
  const box = $("#toast");
  box.textContent = message;
  box.className = error ? "error" : "";
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => {
      box.hidden = true;
    },
    error ? 14000 : 4500,
  );
}

async function api(path, method = "GET", body) {
  const headers = { "Content-Type": "application/json" };
  const token = sessionStorage.getItem("adrobot-token");
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Нет связи с приложением. Проверьте, что сервер запущен.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !$("#auth-dialog").open)
      $("#auth-dialog").showModal();
    throw new Error(data.detail || `Ошибка запроса: ${response.status}`);
  }
  return data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  document.body.setAttribute("aria-busy", "true");
  try {
    await action();
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    document.body.classList.remove("busy");
    document.body.removeAttribute("aria-busy");
  }
}

function errorPage(message) {
  $("#main").innerHTML =
    `<div class="error-panel"><h2>Не удалось загрузить данные</h2><p>${escapeHtml(message)}</p><button class="button secondary" id="retry">Повторить</button></div>`;
  $("#retry").onclick = () => renderRoute();
}

function setNavigation(create) {
  $("#nav-create").classList.toggle("active", create);
  $("#nav-campaigns").classList.toggle("active", !create);
}

async function renderRoute() {
  const version = ++routeVersion;
  window.scrollTo(0, 0);
  campaign = null;
  const hash = location.hash || "#campaigns";
  setNavigation(hash === "#create");
  $("#main").innerHTML = '<div class="loading">Загружаем данные…</div>';
  try {
    if (hash === "#create") {
      const metadata = await api("/metadata");
      if (version === routeVersion) renderCreate(metadata);
    } else if (/^#campaign\/\d+$/.test(hash)) {
      const id = Number(hash.split("/")[1]);
      let document;
      try {
        document = await api(`/campaigns/${id}`);
      } catch (error) {
        if (!error.message.includes("Сначала загрузите")) throw error;
        document = await api(`/campaigns/${id}/sync`, "POST", null);
      }
      if (version === routeVersion) {
        campaign = document;
        renderEditor();
      }
    } else {
      renderListShell();
      await loadCampaigns(version);
    }
  } catch (error) {
    if (version === routeVersion) errorPage(error.message);
  }
}

function renderListShell() {
  $("#main").innerHTML = `
    <div class="page-heading"><div><div class="eyebrow">ВАШЕ РАБОЧЕЕ ПРОСТРАНСТВО</div><h1>Кампании</h1><p>Управляйте потоками и распределением трафика в Keitaro.</p></div><a href="#create" class="button primary">＋ Новая кампания</a></div>
    <div class="intro"><div><h3>От идеи до первого перехода</h3><p>Создайте кампанию за один шаг. Геофильтр, редирект и оффер уже будут на своих местах.</p></div><div class="mini-flow"><div class="flow-node"><b>◎</b>Трафик</div>→<div class="flow-node"><b>⑂</b>Потоки</div>→<div class="flow-node"><b>↗</b>Офферы</div></div></div>
    <section class="panel" aria-label="Список кампаний"><div class="toolbar"><label class="search"><span aria-hidden="true">⌕</span><input id="campaign-search" placeholder="Поиск по названию или ID" aria-label="Поиск кампаний" value="${escapeHtml(query)}"></label><span class="muted small" id="campaign-count"></span></div><div class="table-header"><span>КАМПАНИЯ</span><span>ГРУППА</span><span>СТАТУС</span><span></span></div><div id="campaign-list"><div class="loading">Получаем кампании…</div></div><div class="pagination"><span id="pagination-label"></span><div><button class="button secondary" id="prev">←</button><button class="button secondary" id="next">→</button></div></div></section>`;
  let timer;
  $("#campaign-search").oninput = (event) => {
    query = event.target.value;
    page = 0;
    clearTimeout(timer);
    timer = setTimeout(
      () => loadCampaigns(routeVersion).catch((e) => toast(e.message, true)),
      300,
    );
  };
  $("#prev").onclick = () =>
    run(async () => {
      page--;
      await loadCampaigns(routeVersion);
    });
  $("#next").onclick = () =>
    run(async () => {
      page++;
      await loadCampaigns(routeVersion);
    });
}

let listVersion = 0;
async function loadCampaigns(version) {
  const requestVersion = ++listVersion;
  const result = await api(
    `/campaigns?q=${encodeURIComponent(query)}&offset=${page * 25}&limit=25`,
  );
  if (
    version !== routeVersion ||
    requestVersion !== listVersion ||
    !$("#campaign-list")
  )
    return;
  $("#campaign-count").textContent = `Найдено: ${result.total}`;
  $("#campaign-list").innerHTML = result.items.length
    ? result.items
        .map(
          (c) => `
    <a class="campaign-row" href="#campaign/${c.id}"><div><strong>${escapeHtml(c.name)}</strong><span class="row-id">#${c.id}</span></div><span class="small muted">${escapeHtml(c.group || "Без группы")}</span><span class="tag ${c.state === "active" ? "" : "disabled"}">${c.state === "active" ? "Активна" : "Выключена"}</span><span class="arrow">↗</span></a>`,
        )
        .join("")
    : '<div class="empty"><h3>Кампании не найдены</h3><p>Попробуйте другой запрос или создайте новую кампанию.</p><a href="#create" class="button secondary">Создать кампанию</a></div>';
  $("#pagination-label").textContent = result.total
    ? `${page * 25 + 1}–${Math.min((page + 1) * 25, result.total)} из ${result.total}`
    : "Нет результатов";
  $("#prev").disabled = page === 0;
  $("#next").disabled = (page + 1) * 25 >= result.total;
}

function renderCreate(metadata) {
  const select = (key, preferred) =>
    metadata[key].length
      ? metadata[key]
          .map(
            (item, index) =>
              `<option value="${item.id}" ${item.name === preferred || (metadata[key].length === 1 && index === 0) ? "selected" : ""}>${escapeHtml(item.name)}</option>`,
          )
          .join("")
      : '<option value="">Нет доступных значений</option>';
  $("#main").innerHTML = `
    <div class="breadcrumb"><a href="#campaigns">Кампании</a><span>/</span><span>Новая кампания</span></div>
    <div class="page-heading"><div><div class="eyebrow">СОЗДАНИЕ КАМПАНИИ</div><h1>Настроим первый маршрут</h1><p>Укажите основные данные — два потока создадутся автоматически.</p></div></div>
    <div class="create-layout"><form class="panel" id="create-form">
      <section class="form-section"><div class="section-title"><span>01</span>Основные данные</div><label class="field">Название кампании<input name="name" placeholder="Например, Peru · Flexacil · November" maxlength="200" required autocomplete="off"></label><label class="field">Геокоды стран<input name="geo" id="geo-input" placeholder="AU" required autocomplete="off"><small>Двухбуквенные ISO-коды через запятую: AU, MX, RO.</small></label><label class="field">Оффер Keitaro<div class="offer-picker"><input name="offer_id" id="create-offer-id" type="number" min="1" step="1" placeholder="ID оффера" required><button type="button" class="button secondary" id="create-pick-offer">Выбрать ↗</button></div><span class="picked-offer" id="picked-offer-name"></span></label></section>
      <section class="form-section"><div class="section-title"><span>02</span>Настройки Keitaro</div><label class="field">Домен<select name="domain_id" required>${select("domains")}</select></label><div class="field-pair"><label class="field">Группа<select name="group_id" required>${select("groups", "FORTESTS")}</select></label><label class="field">Источник трафика<select name="traffic_source_id" required>${select("traffic_sources", "FORTESTS")}</select></label></div></section>
      <div class="form-bottom"><span>Кампания появится в вашем Keitaro.</span><button type="submit" class="button primary">Создать кампанию ↗</button></div>
    </form><aside class="route-preview"><div class="eyebrow">ТАК ЭТО БУДЕТ РАБОТАТЬ</div><h3>Два потока. Один маршрут.</h3><p>Трафик проходит потоки по порядку.</p><div class="route-step"><span>ПОТОК 01 · ГЕОФИЛЬТР</span><strong>Из выбранной страны</strong><div class="destination">↗ google.com</div><span id="geo-preview">Страна: ещё не выбрана</span></div><div class="route-step"><span>ПОТОК 02 · ОФФЕР</span><strong>Все остальные посетители</strong><div class="destination" id="offer-preview">↗ Выбранный оффер · 100%</div><span>Без дополнительных фильтров</span></div><p class="preview-note">После создания можно добавить офферы и настроить их веса в редакторе.</p></aside></div>`;
  $("#geo-input").oninput = (e) => {
    $("#geo-preview").textContent =
      `Страны: ${e.target.value.toUpperCase() || "ещё не выбраны"}`;
  };
  $("#create-offer-id").oninput = () => {
    $("#picked-offer-name").textContent = "";
  };
  $("#create-pick-offer").onclick = () => openOffers({ type: "create" });
  $("#create-form").onsubmit = (event) => {
    event.preventDefault();
    run(async () => {
      const values = Object.fromEntries(new FormData(event.target));
      values.geo = values.geo.split(/[,;\s]+/).filter(Boolean);
      for (const key of [
        "offer_id",
        "domain_id",
        "group_id",
        "traffic_source_id",
      ])
        values[key] = Number(values[key]);
      const created = await api("/campaigns", "POST", values);
      toast(`Кампания #${created.id} создана. Оба потока готовы.`);
      location.hash = `campaign/${created.id}`;
    });
  };
}

function renderEditor() {
  const c = campaign;
  if (!c || location.hash !== `#campaign/${c.id}`) return;
  $("#main").innerHTML = `
    <div class="breadcrumb"><a href="#campaigns">Кампании</a><span>/</span><span>#${c.id}</span></div>
    <div class="page-heading"><div><div class="eyebrow">РЕДАКТОР КАМПАНИИ</div><h1>${escapeHtml(c.name)}</h1><div class="meta"><span>#${c.id}</span><span>${escapeHtml(c.group || "Без группы")}</span><span>${escapeHtml(c.domain || "Без домена")}</span></div></div><div class="header-actions"><a class="button secondary" href="${escapeHtml(c.admin_url)}" target="_blank" rel="noopener noreferrer">В Keitaro ↗</a><button class="button primary" id="sync-campaign">↓ Загрузить из Keitaro</button></div></div>
    <div class="sync-note"><span>Синхронизация: ${escapeHtml(new Date(c.synced_at).toLocaleString("ru-RU"))}</span><span>${c.streams.length} потока · ${c.dirty_count ? `${c.dirty_count} с черновиком` : "Всё опубликовано"}</span></div>
    <div id="streams">${c.streams.length ? c.streams.map(renderStream).join("") : '<div class="empty panel"><h3>В кампании пока нет потоков</h3><p>Создайте поток в Keitaro и загрузите данные повторно.</p></div>'}</div>`;
  $("#sync-campaign").onclick = () =>
    run(async () => {
      campaign = await api(`/campaigns/${c.id}/sync`, "POST", {
        revision: c.revision,
      });
      renderEditor();
      toast("Потоки загружены. Изменения из Keitaro учтены.");
    });
  $("#streams").onclick = (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button || busy) return;
    const { action, stream: streamId, offer: offerId } = button.dataset;
    if (action === "add") {
      openOffers({
        type: "stream",
        streamId: Number(streamId),
        campaignId: c.id,
      });
      return;
    }
    run(async () => {
      const base = `/campaigns/${c.id}/streams/${streamId}`;
      if (action === "publish" || action === "cancel") {
        campaign = await api(`${base}/${action}`, "POST", {
          revision: c.revision,
        });
        toast(
          action === "publish"
            ? "Изменения опубликованы в Keitaro."
            : "Черновик отменён.",
        );
      } else {
        const body = {
          revision: c.revision,
          action,
          offer_id: Number(offerId),
        };
        if (action === "pin")
          body.share = Number($(`#share-${streamId}-${offerId}`).value);
        campaign = await api(`${base}/offers`, "POST", body);
      }
      renderEditor();
    });
  };
  $("#streams").onchange = (event) => {
    if (!event.target.matches("input[data-share]") || busy) return;
    const input = event.target;
    const stream = c.streams.find((s) => s.id === Number(input.dataset.stream));
    const offer = stream.offers.find(
      (o) => o.offer_id === Number(input.dataset.offer),
    );
    if (!input.checkValidity()) {
      input.reportValidity();
      input.value = offer.share;
      return;
    }
    run(async () => {
      try {
        campaign = await api(
          `/campaigns/${c.id}/streams/${stream.id}/offers`,
          "POST",
          {
            revision: c.revision,
            action: "pin",
            offer_id: offer.offer_id,
            share: Number(input.value),
          },
        );
        renderEditor();
        toast("Вес закреплён. Остальные доли пересчитаны.");
      } catch (error) {
        input.value = offer.share;
        throw error;
      }
    });
  };
}

function renderStream(stream) {
  const active = stream.offers.filter((o) => o.state === "active").length;
  const button = (action, label, cls = "secondary", disabled = false) =>
    `<button class="button ${cls}" data-action="${action}" data-stream="${stream.id}" ${disabled ? "disabled" : ""}>${label}</button>`;
  return `<section class="stream ${stream.dirty ? "dirty" : ""}" aria-label="${escapeHtml(stream.name)}">
    <div class="stream-heading"><div class="stream-title"><span class="stream-number">${String(stream.position).padStart(2, "0")}</span><div><h3>${escapeHtml(stream.name)}</h3><div class="small">Поток #${stream.id}${stream.state !== "active" ? " · Выключен" : ""}</div></div></div><span class="badge ${stream.dirty ? "draft" : ""}">${stream.missing ? "Удалён в Keitaro" : stream.dirty ? "● Есть черновик" : "✓ Опубликован"}</span></div>
    ${
      !stream.editable
        ? '<div class="opaque">Поток без редактора офферов. Его настройки сохраняются в Keitaro.</div>'
        : `
    <div class="offer-header"><span>ОФФЕР</span><span>ДОЛЯ</span><span>ВЕС</span><span>ДЕЙСТВИЕ</span></div>
    ${stream.offers.map((o) => `<div class="offer-row ${o.state !== "active" ? "removed" : ""}"><div class="offer-name"><span class="row-id">#${o.offer_id}${o.state === "removed" ? " · В архиве" : o.state !== "active" ? " · Отключён" : ""}</span>${escapeHtml(o.name)}</div><div class="weight"><input type="number" min="1" max="100" step="1" id="share-${stream.id}-${o.offer_id}" data-share data-stream="${stream.id}" data-offer="${o.offer_id}" value="${o.share}" aria-label="Вес оффера ${o.offer_id}" ${o.state !== "active" ? "disabled" : ""}><span>%</span></div><button class="pin-button ${o.pinned ? "pinned" : ""}" data-action="${o.pinned ? "unpin" : "pin"}" data-stream="${stream.id}" data-offer="${o.offer_id}" aria-pressed="${o.pinned}" ${o.state !== "active" ? "disabled" : ""}>${o.pinned ? "◆ Закреплён" : "◇ Закрепить"}</button><button class="remove-button ${o.state !== "active" ? "restore-button" : ""}" data-action="${o.state === "active" ? "remove" : "restore"}" data-stream="${stream.id}" data-offer="${o.offer_id}">${o.state === "active" ? "Убрать" : "Вернуть ↩"}</button></div>`).join("")}
    <div class="stream-footer">${button("add", "＋ Добавить оффер")}<span class="total ${stream.total !== 100 ? "invalid" : ""}">${active} активных · Всего<b>${stream.total}%</b></span><div class="header-actions">${button("cancel", "Отменить", "quiet", !stream.dirty)}${button("publish", "↑ Опубликовать", "primary", !stream.dirty || stream.total !== 100 || active === 0)}</div></div>`
    }
  </section>`;
}

function openOffers(target) {
  offerTarget = target;
  offerQuery = "";
  offerPage = 0;
  $("#offer-search").value = "";
  $("#offer-results").innerHTML = '<div class="loading">Получаем офферы…</div>';
  $("#offer-dialog").showModal();
  loadOffers().catch((e) => {
    $("#offer-results").textContent = e.message;
  });
}

async function loadOffers(append = false) {
  const version = ++offerSearchVersion;
  const result = await api(
    `/offers?q=${encodeURIComponent(offerQuery)}&offset=${offerPage * 30}&limit=30`,
  );
  if (version !== offerSearchVersion) return;
  const stream =
    offerTarget.type === "stream"
      ? campaign?.streams.find((s) => s.id === offerTarget.streamId)
      : null;
  const html = result.items
    .map((o) => {
      const exists = stream?.offers.some((item) => item.offer_id === o.id);
      return `<button type="button" class="offer-option" data-id="${o.id}" ${exists ? "disabled" : ""}><span class="row-id">#${o.id}${exists ? " · Уже в потоке или архиве" : ""}</span>${escapeHtml(o.name)}</button>`;
    })
    .join("");
  if (append) $("#offer-results").insertAdjacentHTML("beforeend", html);
  else
    $("#offer-results").innerHTML =
      html ||
      '<div class="empty">Офферы не найдены. Попробуйте другое название или ID.</div>';
  $("#offer-count").textContent = `Найдено: ${result.total}`;
  $("#offers-more").hidden = (offerPage + 1) * 30 >= result.total;
  $("#offer-results").onclick = (event) => {
    const button = event.target.closest("button[data-id]");
    if (!button || button.disabled) return;
    const selected = result.items.find(
      (o) => o.id === Number(button.dataset.id),
    ) || { id: Number(button.dataset.id), name: button.textContent };
    if (offerTarget.type === "create") {
      $("#create-offer-id").value = selected.id;
      $("#picked-offer-name").textContent = selected.name;
      $("#offer-preview").textContent = `↗ Оффер #${selected.id} · 100%`;
      $("#offer-dialog").close();
    } else {
      run(async () => {
        campaign = await api(
          `/campaigns/${offerTarget.campaignId}/streams/${offerTarget.streamId}/offers`,
          "POST",
          { revision: campaign.revision, action: "add", offer_id: selected.id },
        );
        $("#offer-dialog").close();
        renderEditor();
        toast("Оффер добавлен в черновик. Веса пересчитаны.");
      });
    }
  };
}

let offerTimer;
$("#offer-search").oninput = (e) => {
  offerQuery = e.target.value;
  offerPage = 0;
  clearTimeout(offerTimer);
  ++offerSearchVersion;
  offerTimer = setTimeout(
    () =>
      loadOffers().catch((e) => {
        $("#offer-results").textContent = e.message;
      }),
    250,
  );
};
$("#offers-more").onclick = () =>
  run(async () => {
    offerPage++;
    await loadOffers(true);
  });
document.querySelectorAll("[data-close]").forEach((button) => {
  button.onclick = () => $(`#${button.dataset.close}`).close();
});
$("#auth-form").onsubmit = (event) => {
  event.preventDefault();
  sessionStorage.setItem("adrobot-token", $("#access-token").value);
  $("#access-token").value = "";
  $("#auth-dialog").close();
  initialize();
};
window.addEventListener("hashchange", renderRoute);

async function initialize() {
  try {
    const config = await api("/config");
    $("#connection").textContent = config.configured
      ? "Keitaro настроен"
      : "Нужна настройка .env";
    $("#connection").classList.toggle("error", !config.configured);
    await renderRoute();
  } catch (error) {
    $("#connection").textContent = "Нет подключения";
    $("#connection").classList.add("error");
    errorPage(error.message);
  }
}
initialize();
