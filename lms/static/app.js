/* Corporate LMS browser client.
 * No inline event handlers or inline styles: the server sends a strict CSP.
 */
'use strict';

const state = {
  token: '',
  me: null,
  permissions: new Set(),
  employees: [],
  courses: [],
  course: null,
  lessonId: null,
  questionLesson: null,
  questionCache: [],
  csvText: '',
  csvName: '',
  view: 'dashboard',
  catalogQuery: {},
  cohorts: [],
  paths: [],
  badges: [],
  gradebook: null,
  discussions: [],
  importSignature: '',
  importSnapshot: null,
  videoRefreshTimer: null,
};

let sessionGeneration = 0;
let playerGeneration = 0;
let lastRouteHash = '';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const ROLE_DEFAULTS = {
  platform_admin: 'demo-admin',
  learning_admin: 'demo-admin',
  compliance_officer: 'demo-admin',
  auditor: 'demo-admin',
  manager: 'E1002',
  learner: 'E1003',
  instructor: 'E1004',
};

const LESSON_LABELS = {
  text: 'Article', video: 'Video', youtube: 'YouTube', link: 'Link', quiz: 'Quiz',
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function can(permission) {
  return state.permissions.has(permission);
}

function isCurrent(generation) {
  return generation === sessionGeneration;
}

function canManagePaths() {
  return can('manage_content') || can('manage_learning');
}

function canStartAssignment() {
  return can('complete_any') || can('manage_learning') ||
    (can('complete_team') && can('read_team'));
}

function canCompleteAssignment() {
  return can('complete_any') || (can('complete_team') && can('read_team'));
}

function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  const alert = $('#alertToast');
  if (alert) alert.textContent = isError ? message : '';
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.remove('show'), 4000);
}

function authHeaders() {
  return state.token ? { Authorization: 'Bearer ' + state.token } : {};
}

async function rawFetch(path, options = {}) {
  const request = { ...options };
  delete request.retry;
  const headers = () => ({ ...authHeaders(), ...(request.headers || {}) });
  let response = await fetch(path, { ...request, headers: headers() });
  if (response.status === 401 && state.token) {
    try { await refreshSession({ reload: false }); } catch (_) { /* retry once */ }
    response = await fetch(path, { ...request, headers: headers() });
  }
  return response;
}

async function api(path, options = {}) {
  const request = { ...options };
  delete request.retry;
  const response = await rawFetch(path, {
    ...request,
    headers: { 'Content-Type': 'application/json', ...(request.headers || {}) },
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = { message: text }; }
  }
  if (!response.ok) {
    throw new Error(data.message || data.error || `Request failed (${response.status})`);
  }
  return data;
}

async function createSession(role, actor) {
  const response = await fetch('/api/v1/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, actor }),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.message || data.error || 'Unable to create session');
  state.token = data.token || '';
}

async function refreshSession({ reload = true } = {}) {
  const role = $('#role').value;
  const actor = $('#actor').value.trim() || ROLE_DEFAULTS[role] || 'demo-admin';
  $('#actor').value = actor;
  await createSession(role, actor);
  const generation = ++sessionGeneration;
  playerGeneration += 1;
  if (state.videoRefreshTimer) {
    clearTimeout(state.videoRefreshTimer);
    state.videoRefreshTimer = null;
  }
  const me = await api('/api/v1/me');
  if (!isCurrent(generation)) return;
  state.me = me;
  state.permissions = new Set(me.permissions || []);
  state.employees = [];
  state.courses = [];
  state.cohorts = [];
  state.paths = [];
  state.badges = [];
  state.discussions = [];
  state.course = null;
  state.lessonId = null;
  state.questionCache = [];
  state.gradebook = null;
  state.csvText = '';
  state.csvName = '';
  state.importSignature = '';
  invalidateImport('');
  const leftCourse = state.view === 'course';
  if (leftCourse) {
    const mount = $('#courseDetail');
    if (mount) mount.innerHTML = '';
    activateView('catalog');
  }
  applyPermissions();
  updateBell(me.unread_notifications || 0);
  if (reload) await loadView(state.view);
  else if (leftCourse) await loadCatalog();
}

function applyPermissions() {
  $$('[data-perm]').forEach((el) => {
    const allowed = can(el.dataset.perm);
    el.hidden = !allowed;
    el.classList.toggle('is-hidden', !allowed);
  });
  $$('[data-perm-any]').forEach((el) => {
    const permissions = String(el.dataset.permAny).split(/[\s,]+/).filter(Boolean);
    const allowed = permissions.some((permission) => can(permission));
    el.hidden = !allowed;
    el.classList.toggle('is-hidden', !allowed);
  });
  const activeNav = $('nav button.active');
  if (activeNav && activeNav.hidden) showView('dashboard', false);
}

function updateBell(count) {
  const badge = $('#bellCount');
  badge.textContent = String(count);
  badge.hidden = !count;
}

/* ---------------------------------------------------------------- charts */

function chart(title, data, emptyText = 'No data') {
  if (!data || !data.length) {
    return `<article><h2>${esc(title)}</h2><div class="empty">${esc(emptyText)}</div></article>`;
  }
  const max = Math.max(1, ...data.map((x) => Number(x.value) || 0));
  const rows = data.map((x) => {
    const width = Math.round(100 * (Number(x.value) || 0) / max);
    return `<div class="chart-row"><span>${esc(x.label || 'Unassigned')}</span>` +
      `<div class="bar"><i data-w="${width}"></i></div><strong>${esc(x.value)}</strong></div>`;
  }).join('');
  return `<article><h2>${esc(title)}</h2><div class="chart">${rows}</div></article>`;
}

function hydrate(root) {
  $$('.bar > i[data-w]', root).forEach((bar) => {
    bar.style.width = bar.dataset.w + '%';
  });
}

function kpis(items) {
  return `<div class="kpis">${items.map(([label, value]) =>
    `<div class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('')}</div>`;
}

function statusTag(value) {
  const map = {
    completed: 'green', active: 'green', applied: 'green', published: 'green',
    assigned: 'amber', in_progress: 'amber', future: 'amber', leave: 'amber',
    draft: 'amber',
    failed: 'red', inactive: 'red', terminated: 'red', exempted: 'red',
    archived: '',
  };
  const label = String(value || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return `<span class="tag ${map[value] || ''}">${esc(label)}</span>`;
}

function table(headers, rows) {
  if (!rows.length) return '<div class="empty">Nothing to show.</div>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead>` +
    `<tbody>${rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

/* -------------------------------------------------------------- dashboard */

async function loadDashboard() {
  const generation = sessionGeneration;
  try {
    const data = await api('/api/v1/dashboard');
    if (!isCurrent(generation)) return;
    const scope = data.scope || 'personal';
    const title = { organization: 'Workforce learning overview', team: 'Team learning overview', personal: 'My learning overview' }[scope];
    const subtitle = {
      organization: 'Compliance, readiness, and operational health across the organization.',
      team: 'Progress for your reporting line, at every depth.',
      personal: 'Your assignments, deadlines and completion evidence.',
    }[scope];
    $('#dashboardTitle').textContent = title;
    $('#dashboardSubtitle').textContent = subtitle;

    let html = '';
    if (scope === 'organization') {
      html += kpis([
        ['Active employees', data.active_employees],
        ['Active courses', data.active_courses],
        ['Assignments', data.total_assignments],
        ['Completion rate', `${data.completion_rate}%`],
        ['Overdue', data.overdue_assignments],
        ['Recertification due (30d)', data.recertifications_due_30d],
      ]);
      html += '<div class="grid two">' + chart('Assignments by status', data.assignment_status) +
        chart('Active employees by department', data.departments) + '</div>';
      const compliance = (data.compliance_by_department || []).map((row) => [
        esc(row.label), esc(row.total), esc(row.completed), `${esc(row.rate)}%`,
      ]);
      html += `<article><h2>Compliance by department</h2>${table(['Department', 'Assignments', 'Completed', 'Rate'], compliance)}</article>`;
    } else if (scope === 'team') {
      html += kpis([
        ['Team members', data.team_size],
        ['Assignments', data.total_assignments],
        ['Completed', data.completed_assignments],
        ['Completion rate', `${data.completion_rate}%`],
        ['Overdue', data.overdue_assignments],
      ]);
      html += '<div class="grid two">' + chart('Assignments by status', data.assignment_status) + '</div>';
      const members = (data.members || []).map((member) => [
        `<strong>${esc(member.display_name)}</strong><br><small>${esc(member.employee_id)}</small>`,
        esc(member.department || '—'),
        esc(member.assignments),
        esc(member.completed),
        member.overdue ? `<span class="tag red">${esc(member.overdue)}</span>` : '0',
      ]);
      html += `<article><h2>Team members</h2>${table(['Member', 'Department', 'Assignments', 'Completed', 'Overdue'], members)}</article>`;
    } else {
      html += kpis([
        ['Open assignments', data.total_assignments],
        ['Completed', data.completed_assignments],
        ['In progress', data.in_progress_assignments],
        ['Overdue', data.overdue_assignments],
        ['Completion rate', `${data.completion_rate}%`],
        ['Unread alerts', data.unread_notifications],
      ]);
      html += '<div class="grid two">' + chart('Assignments by status', data.assignment_status) + '</div>';
      const upcoming = (data.upcoming || []).map((item) => [
        `<strong>${esc(item.course_title)}</strong><br><small>${esc(item.course_code)}</small>`,
        esc(item.due_date || '—'),
        statusTag(item.status),
      ]);
      html += `<article><h2>Upcoming deadlines</h2>${table(['Course', 'Due', 'Status'], upcoming)}</article>`;
    }
    const body = $('#dashboardBody');
    body.innerHTML = html;
    hydrate(body);
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ----------------------------------------------------------------- catalog */

function courseThumb(course) {
  if (course.thumbnail) {
    return `<img class="course-thumb" src="${esc(course.thumbnail)}" alt="" loading="lazy">`;
  }
  const initials = String(course.course_code || '?').slice(0, 2).toUpperCase();
  return `<div class="course-thumb placeholder" aria-hidden="true">${esc(initials)}</div>`;
}

function progressBar(percent, label) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  return `<div class="progress"><div class="progress-head"><span>${esc(label || 'Progress')}</span><strong>${value}%</strong></div>` +
    `<div class="bar"><i data-w="${value}"></i></div></div>`;
}

function typeBadge(type) {
  return `<span class="tag type-${esc(type)}">${esc(LESSON_LABELS[type] || type)}</span>`;
}

function courseCard(course) {
  const progress = course.my_progress || {};
  const assignment = course.assignment;
  const pct = progress.percent || 0;
  const action = pct > 0 ? 'Continue' : 'Open';
  return `<article class="card course-card">
    <div class="card-cover">${courseThumb(course)}
      <div class="cover-tags">${course.mandatory ? '<span class="tag red">Mandatory</span>' : ''}
      ${course.status !== 'published' ? statusTag(course.status) : ''}</div>
    </div>
    <div class="card-body">
      <h3>${esc(course.title)}</h3>
      <small>${esc(course.course_code)} · ${esc(course.category)}${course.level ? ` · ${esc(course.level)}` : ''}</small>
      <p>${esc(course.summary || course.description || '')}</p>
      <div class="meta">
        <span class="tag">${esc(course.lesson_count)} lessons</span>
        <span class="tag">${esc(course.module_count)} modules</span>
        <span class="tag">${esc(course.content_minutes || course.duration_minutes)} min</span>
        ${course.author ? `<span class="tag">${esc(course.author)}</span>` : ''}
      </div>
      ${progress.required_lessons ? progressBar(pct, `${progress.completed_required}/${progress.required_lessons} required lessons`) : ''}
      ${assignment ? `<div class="meta">${statusTag(assignment.status)}${assignment.due_date ? `<span class="tag">Due ${esc(assignment.due_date)}</span>` : ''}</div>` : ''}
      <div class="actions"><button type="button" data-action="open-course" data-course="${esc(course.course_code)}">${action} course</button></div>
    </div>
  </article>`;
}

async function loadCatalog(query = null) {
  const generation = sessionGeneration;
  try {
    if (query) state.catalogQuery = query;
    const params = new URLSearchParams();
    Object.entries(state.catalogQuery).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const suffix = params.toString() ? '?' + params.toString() : '';
    state.courses = await api('/api/v1/courses' + suffix);
    if (!isCurrent(generation)) return;
    const target = $('#courseCards');
    target.innerHTML = state.courses.length
      ? state.courses.map(courseCard).join('')
      : '<div class="empty">No courses match this filter.</div>';
    hydrate(target);
    $('#catalogMeta').textContent = `${state.courses.length} course(s)`;
    const select = $('#policyCourse');
    if (select) {
      select.innerHTML = state.courses.map((course) =>
        `<option value="${esc(course.course_code)}">${esc(course.course_code)} — ${esc(course.title)}</option>`).join('');
    }
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ------------------------------------------------------------ course player */

function findLesson(course, lessonId) {
  if (!course) return null;
  for (const module of course.modules || []) {
    for (const lesson of module.lessons || []) {
      if (String(lesson.id) === String(lessonId)) return { lesson, module };
    }
  }
  return null;
}

function defaultLessonId(course) {
  let first = null;
  for (const module of course.modules || []) {
    for (const lesson of module.lessons || []) {
      if (!first) first = lesson.id;
      if (!lesson.progress || lesson.progress.status !== 'completed') return lesson.id;
    }
  }
  return first;
}

async function openCourse(code, lessonId = null) {
  const generation = ++playerGeneration;
  try {
    const course = await api('/api/v1/courses/' + encodeURIComponent(code));
    if (generation !== playerGeneration) return;
    state.course = course;
    state.lessonId = lessonId || defaultLessonId(course);
    activateView('course');
    renderCourse();
    await renderPlayer(generation);
  } catch (error) {
    if (generation === playerGeneration) toast(error.message, true);
  }
}

async function refreshCourse(render = true) {
  if (!state.course) return null;
  const generation = ++playerGeneration;
  const code = state.course.course_code;
  const course = await api('/api/v1/courses/' + encodeURIComponent(code));
  if (generation !== playerGeneration) return null;
  state.course = course;
  if (render) {
    renderCourse();
    await renderPlayer(generation);
  }
  return course;
}

function curriculumHtml(course) {
  const admin = can('manage_content');
  const blocks = (course.modules || []).map((module) => {
    const lessons = (module.lessons || []).map((lesson) => {
      const done = lesson.progress && lesson.progress.status === 'completed';
      const active = String(lesson.id) === String(state.lessonId);
      return `<li class="lesson-row${active ? ' active' : ''}">
        <button type="button" class="lesson-link" data-action="select-lesson" data-lesson="${lesson.id}">
          <span class="lesson-dot${done ? ' done' : ''}">${done ? '✓' : ''}</span>
          <span class="lesson-name">${esc(lesson.title)}</span>
          <span class="lesson-meta">${typeBadge(lesson.type)}${lesson.duration_minutes ? `<small>${esc(lesson.duration_minutes)}m</small>` : ''}</span>
        </button>
        ${admin ? `<div class="mini-actions">
          <button type="button" class="icon" data-action="edit-lesson" data-lesson="${lesson.id}">Edit</button>
          ${lesson.type === 'quiz' ? `<button type="button" class="icon" data-action="manage-questions" data-lesson="${lesson.id}">Questions (${esc(lesson.question_count)})</button>` : ''}
          <button type="button" class="icon danger" data-action="delete-lesson" data-lesson="${lesson.id}">Delete</button>
        </div>` : ''}
      </li>`;
    }).join('') || '<li class="muted small">No lessons yet.</li>';
    return `<section class="module-block">
      <div class="module-head">
        <h3>${esc(module.title)}</h3>
        ${admin ? `<div class="mini-actions">
          <button type="button" class="icon" data-action="edit-module" data-module="${module.id}">Edit</button>
          <button type="button" class="icon danger" data-action="delete-module" data-module="${module.id}">Delete</button>
        </div>` : ''}
      </div>
      ${module.summary ? `<p class="muted small">${esc(module.summary)}</p>` : ''}
      <ol class="lesson-list">${lessons}</ol>
      ${admin ? `<button type="button" class="secondary small" data-action="new-lesson" data-module="${module.id}">Add lesson</button>` : ''}
    </section>`;
  }).join('') || '<div class="empty">No modules yet.</div>';
  return blocks;
}

function renderCourse() {
  const course = state.course;
  const host = $('#courseDetail');
  if (!course) { host.innerHTML = '<div class="empty">Course not loaded.</div>'; return; }
  const admin = can('manage_content');
  const progress = course.progress || {};
  const assignment = course.assignment;
  const objectives = (course.objectives_list || []).length
    ? `<ul class="objectives">${course.objectives_list.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`
    : '';
  const tags = (course.tags_list || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join(' ');
  const prerequisites = (course.prerequisite_list || []).length
    ? `<p class="muted">Prerequisites: ${course.prerequisite_list.map((code) => `<span class="tag">${esc(code)}</span>`).join(' ')}</p>`
    : '';
  const enroll = course.is_published && !assignment
    ? `<button type="button" data-action="enroll" data-course="${esc(course.course_code)}">Enroll in this course</button>`
    : '';

  host.innerHTML = `
    <article class="course-hero">
      <div class="hero-media">${courseThumb(course)}</div>
      <div class="hero-body">
        <button type="button" class="back-link" data-action="back-catalog">← Back to catalog</button>
        <div class="meta">
          ${statusTag(course.status)}
          ${course.mandatory ? '<span class="tag red">Mandatory</span>' : ''}
          <span class="tag">${esc(course.category)}</span>
          ${course.level ? `<span class="tag">${esc(course.level)}</span>` : ''}
          ${course.language ? `<span class="tag">${esc(course.language)}</span>` : ''}
        </div>
        <h1>${esc(course.title)}</h1>
        <p class="lead">${esc(course.summary || course.description || '')}</p>
        <div class="meta">
          <span class="tag">${esc(course.lesson_count)} lessons</span>
          <span class="tag">${esc(course.module_count)} modules</span>
          <span class="tag">${esc(course.content_minutes || course.duration_minutes)} min</span>
          ${course.author ? `<span class="tag">By ${esc(course.author)}</span>` : ''}
        </div>
        ${progress.required_lessons ? progressBar(progress.percent, `${progress.completed_required}/${progress.required_lessons} required lessons`) : ''}
        ${assignment ? `<div class="meta">${statusTag(assignment.status)}${assignment.due_date ? `<span class="tag">Due ${esc(assignment.due_date)}</span>` : ''}${assignment.recert_due_date ? `<span class="tag">Recertify by ${esc(assignment.recert_due_date)}</span>` : ''}</div>` : ''}
        <div class="actions">${enroll}${admin ? `<button type="button" class="secondary" data-action="edit-course">Edit details</button>` : ''}</div>
      </div>
    </article>
    <div class="course-about">
      <article>
        <h2>About this course</h2>
        <p class="prewrap">${esc(course.description || 'No description yet.')}</p>
        ${objectives ? `<h3>What you will learn</h3>${objectives}` : ''}
        ${prerequisites}
        ${tags ? `<div class="meta">${tags}</div>` : ''}
      </article>
    </div>
    <div class="course-layout">
      <div id="playerMount" class="player-pane"></div>
      <aside class="curriculum">
        <div class="curriculum-head">
          <h2>Curriculum</h2>
          ${admin ? `<button type="button" class="secondary small" data-action="new-module">Add module</button>` : ''}
        </div>
        <div id="curriculumList">${curriculumHtml(course)}</div>
      </aside>
    </div>`;
  hydrate(host);
}

function playerShell(module, lesson, body, done) {
  const isQuiz = lesson.type === 'quiz';
  const footer = isQuiz
    ? ''
    : done
      ? '<span class="tag green">Completed</span>'
      : `<button type="button" data-action="complete-lesson" data-lesson="${lesson.id}">Mark complete</button>`;
  return `<article class="player-card">
    <div class="player-head">
      <div>
        <small class="muted">${esc(module.title)}</small>
        <h2>${esc(lesson.title)}</h2>
      </div>
      <div class="meta">
        ${typeBadge(lesson.type)}
        ${lesson.duration_minutes ? `<span class="tag">${esc(lesson.duration_minutes)} min</span>` : ''}
        ${lesson.is_required ? '<span class="tag">Required</span>' : '<span class="tag">Optional</span>'}
      </div>
    </div>
    <div class="player-body">${body}</div>
    <div class="player-foot">${footer}</div>
  </article>`;
}

async function renderPlayer(generation = null) {
  const token = generation === null ? ++playerGeneration : generation;
  if (state.videoRefreshTimer) {
    clearTimeout(state.videoRefreshTimer);
    state.videoRefreshTimer = null;
  }
  const mount = $('#playerMount');
  if (!mount || token !== playerGeneration) return;
  const found = findLesson(state.course, state.lessonId);
  if (!found) {
    mount.innerHTML = '<div class="empty">Select a lesson to begin.</div>';
    return;
  }
  const { lesson, module } = found;
  const done = Boolean(lesson.progress && lesson.progress.status === 'completed');
  let body = '';
  if (lesson.type === 'text') {
    body = `<div class="lesson-text">${esc(lesson.content)}</div>`;
  } else if (lesson.type === 'youtube') {
    body = `<div class="video-frame"><iframe
      src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(lesson.youtube_id || '')}?rel=0"
      title="${esc(lesson.title)}"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      allowfullscreen></iframe></div>`;
  } else if (lesson.type === 'video') {
    body = lesson.stream_url
      ? `<video id="lessonVideo" controls preload="metadata" src="${esc(lesson.stream_url)}"></video>`
      : lesson.content
        ? '<div class="notice">This lesson points to an external video URL. Upload a video file to play it here — external media is blocked by the content security policy.</div>'
        : '<div class="empty">No video attached to this lesson.</div>';
  } else if (lesson.type === 'link') {
    body = `<p>This lesson opens an external resource.</p>
      <p><a class="button-link" href="${esc(lesson.content)}" target="_blank" rel="noopener noreferrer">Open resource ↗</a></p>`;
  } else if (lesson.type === 'quiz') {
    await renderQuiz(lesson, module, done, token);
    return;
  }
  if (token !== playerGeneration) return;
  mount.innerHTML = playerShell(module, lesson, body, done);
  if (lesson.type === 'video') attachVideoProgress(lesson);
}

function scheduleVideoRefresh(lesson, video, generation) {
  let expires = 0;
  try {
    expires = Number(new URL(video.src, window.location.href).searchParams.get('exp'));
  } catch (_) {
    return;
  }
  if (!expires) return;
  const delay = Math.max(1000, expires * 1000 - Date.now() - 60000);
  state.videoRefreshTimer = setTimeout(async () => {
    state.videoRefreshTimer = null;
    if (generation !== playerGeneration || !video.isConnected || !state.course) return;
    try {
      const freshCourse = await api(
        `/api/v1/courses/${encodeURIComponent(state.course.course_code)}`
      );
      const found = findLesson(freshCourse, lesson.id);
      const fresh = found && found.lesson;
      if (!fresh || !fresh.stream_url) return;
      const position = video.currentTime;
      const wasPlaying = !video.paused && !video.ended;
      const restore = () => {
        try { video.currentTime = position; } catch (_) { /* stream not seekable yet */ }
      };
      video.addEventListener('loadedmetadata', restore, { once: true });
      video.src = fresh.stream_url;
      if (wasPlaying) video.play().catch(() => {});
      scheduleVideoRefresh(fresh, video, generation);
    } catch (_) {
      // A failed refresh leaves the current URL in place; playback may continue.
    }
  }, delay);
}

function attachVideoProgress(lesson) {
  const video = $('#lessonVideo');
  if (!video) return;
  let last = 0;
  video.addEventListener('timeupdate', () => {
    if (video.currentTime - last >= 10) {
      last = video.currentTime;
      api(`/api/v1/lessons/${lesson.id}/progress`, {
        method: 'POST',
        body: JSON.stringify({ position_seconds: Math.round(video.currentTime) }),
      }).catch(() => {});
    }
  });
  video.addEventListener('ended', () => {
    completeLesson(lesson.id).catch((error) => toast(error.message, true));
  });
  scheduleVideoRefresh(lesson, video, playerGeneration);
}

async function renderQuiz(lesson, module, done, token = playerGeneration) {
  const mount = $('#playerMount');
  try {
    const quiz = await api(`/api/v1/lessons/${lesson.id}/quiz`);
    if (token !== playerGeneration) return;
    if (!quiz.questions.length) {
      mount.innerHTML = playerShell(module, lesson,
        '<div class="empty">This quiz has no questions yet.</div>', done);
      return;
    }
    const fields = quiz.questions.map((question, index) => `
      <fieldset class="quiz-q">
        <legend>${index + 1}. ${esc(question.prompt)}</legend>
        ${question.options.map((option, optionIndex) => `
          <label class="option"><input type="radio" name="q_${question.id}" value="${optionIndex}" ${done ? 'disabled' : ''}> ${esc(option)}</label>`).join('')}
      </fieldset>`).join('');
    const footer = done
      ? '<span class="tag green">Passed</span>'
      : `<button type="submit">Submit answers</button>`;
    mount.innerHTML = `<article class="player-card">
      <div class="player-head">
        <div><small class="muted">${esc(module.title)}</small><h2>${esc(lesson.title)}</h2></div>
        <div class="meta">${typeBadge('quiz')}<span class="tag">Pass ${esc(quiz.pass_score)}%</span></div>
      </div>
      <form class="player-body quiz-form" data-form="quiz" data-lesson="${lesson.id}">
        ${lesson.content ? `<p class="prewrap">${esc(lesson.content)}</p>` : ''}
        ${fields}
        <div class="player-foot">${footer}</div>
      </form>
    </article>`;
  } catch (error) {
    toast(error.message, true);
  }
}

function renderQuizResult(lessonId, result) {
  const mount = $('#playerMount');
  if (!mount) return;
  mount.innerHTML = `<article class="player-card">
    <div class="player-head"><div><h2>Quiz result</h2></div>
      <div class="meta">${result.passed ? '<span class="tag green">Passed</span>' : '<span class="tag red">Not yet</span>'}</div></div>
    <div class="player-body">
      <div class="score ${result.passed ? 'pass' : 'fail'}">${esc(result.score)}%</div>
      <p>${esc(result.correct)} of ${esc(result.total)} correct. Pass mark ${esc(result.pass_score)}%.</p>
      <ol class="quiz-results">${result.results.map((item, index) => `
        <li class="${item.correct ? 'ok' : 'bad'}"><strong>Question ${index + 1}: ${item.correct ? 'correct' : 'incorrect'}</strong>
        ${item.explanation ? `<div class="muted">${esc(item.explanation)}</div>` : ''}</li>`).join('')}</ol>
    </div>
    <div class="player-foot"><button type="button" data-action="select-lesson" data-lesson="${lessonId}">Back to lesson</button></div>
  </article>`;
}

async function completeLesson(lessonId) {
  const result = await api(`/api/v1/lessons/${lessonId}/progress`, {
    method: 'POST',
    body: JSON.stringify({ completed: true }),
  });
  toast('Lesson complete');
  await refreshCourse();
  if (result.assignment && result.assignment.status === 'completed') {
    toast('Course completed — assignment recorded');
  }
}

/* ------------------------------------------------------------ authoring UI */

function setForm(form, values) {
  Object.entries(values || {}).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value === null || value === undefined ? '' : value;
  });
}

function openCourseDialog(course = null) {
  const form = $('#courseDialog form');
  form.reset();
  form.original_code.value = course ? course.course_code : '';
  form.course_code.disabled = Boolean(course);
  $('#courseDialogTitle').textContent = course ? 'Edit course' : 'New course';
  $('#courseThumbName').textContent = '';
  $('#courseThumbFile').value = '';
  if (course) {
    setForm(form, {
      course_code: course.course_code, title: course.title, category: course.category,
      level: course.level, language: course.language, author: course.author,
      duration_minutes: course.duration_minutes, version: course.version,
      recertification_days: course.recertification_days,
      passing_score: course.passing_score, status: course.status,
      mandatory: course.mandatory, summary: course.summary,
      description: course.description, objectives: course.objectives,
      tags: course.tags, prerequisite_codes: course.prerequisite_codes,
      thumbnail_url: course.thumbnail_media_id ? '' : (course.thumbnail_url || ''),
      thumbnail_media_id: course.thumbnail_media_id || '',
      content_url: course.content_url || '',
    });
    $('#courseThumbName').textContent = course.thumbnail ? 'An image is currently set.' : '';
  }
  openDialog('courseDialog');
}

function openModuleDialog(module = null) {
  const form = $('#moduleDialog form');
  form.reset();
  form.course_code.value = state.course ? state.course.course_code : '';
  form.module_id.value = module ? module.id : '';
  $('#moduleDialogTitle').textContent = module ? 'Edit module' : 'Add module';
  if (module) setForm(form, { title: module.title, summary: module.summary });
  openDialog('moduleDialog');
}

function syncLessonFields() {
  const type = $('#lessonType').value;
  $$('[data-show-for]').forEach((el) => {
    el.hidden = !el.dataset.showFor.split(' ').includes(type);
  });
}

function openLessonDialog(moduleId, lesson = null) {
  const form = $('#lessonDialog form');
  form.reset();
  form.module_id.value = moduleId;
  form.lesson_id.value = lesson ? lesson.id : '';
  form.media_id.value = lesson && lesson.media_id ? lesson.media_id : '';
  form.title.value = lesson ? lesson.title : '';
  form.lesson_type.value = lesson ? lesson.type : 'text';
  form.duration_minutes.value = lesson ? lesson.duration_minutes : 5;
  form.is_required.checked = lesson ? Boolean(lesson.is_required) : true;
  form.quiz_pass_score.value = lesson && lesson.quiz_pass_score != null ? lesson.quiz_pass_score : 70;
  form.content.value = lesson && ['text', 'quiz'].includes(lesson.type) ? (lesson.content || '') : '';
  form.url_content.value = lesson && ['youtube', 'link'].includes(lesson.type)
    ? (lesson.content || '')
    : (lesson && lesson.type === 'video' && !lesson.media_id ? (lesson.content || '') : '');
  $('#lessonVideoName').textContent = lesson && lesson.media_id ? 'An uploaded video is attached.' : 'No file uploaded.';
  $('#lessonVideoFile').value = '';
  $('#lessonDialogTitle').textContent = lesson ? 'Edit lesson' : 'Add lesson';
  syncLessonFields();
  openDialog('lessonDialog');
}

async function openQuestionManager(lessonId) {
  const quiz = await api(`/api/v1/lessons/${lessonId}/quiz`);
  state.questionLesson = lessonId;
  state.questionCache = quiz.questions;
  $('#questionManagerTitle').textContent = `Questions — ${quiz.title}`;
  $('#questionList').innerHTML = quiz.questions.length
    ? quiz.questions.map((question, index) => `<article class="question-item">
        <div class="meta"><span class="tag">Q${index + 1}</span>
          <span class="tag green">Answer ${Number(question.correct_index) + 1}</span>
          <button type="button" class="icon" data-action="edit-question" data-question="${question.id}">Edit</button>
          <button type="button" class="icon danger" data-action="delete-question" data-question="${question.id}">Delete</button>
        </div>
        <p><strong>${esc(question.prompt)}</strong></p>
        <ol class="option-list">${question.options.map((option, optionIndex) =>
          `<li class="${optionIndex === Number(question.correct_index) ? 'correct' : ''}">${esc(option)}</li>`).join('')}</ol>
        ${question.explanation ? `<p class="muted">${esc(question.explanation)}</p>` : ''}
      </article>`).join('')
    : '<div class="empty">No questions yet.</div>';
  openDialog('questionManagerDialog');
}

function openQuestionDialog(question = null) {
  const form = $('#questionDialog form');
  form.reset();
  form.lesson_id.value = state.questionLesson;
  form.question_id.value = question ? question.id : '';
  $('#questionDialogTitle').textContent = question ? 'Edit question' : 'Add question';
  if (question) {
    setForm(form, {
      prompt: question.prompt,
      options: (question.options || []).join('\n'),
      correct_index: Number(question.correct_index) + 1,
      explanation: question.explanation || '',
    });
  }
  openDialog('questionDialog');
}

async function uploadMedia(file) {
  const response = await rawFetch('/api/v1/media', {
    method: 'POST',
    headers: { 'Content-Type': file.type, 'X-Filename': file.name },
    body: file,
  });
  const text = await response.text();
  let data = {};
  if (text) { try { data = JSON.parse(text); } catch { data = { message: text }; } }
  if (!response.ok) throw new Error(data.message || `Upload failed (${response.status})`);
  return data;
}

/* --------------------------------------------------------------- learning */

async function learnerOptions() {
  const generation = sessionGeneration;
  if (!state.employees.length) {
    const employees = await api('/api/v1/learners');
    if (!isCurrent(generation)) return [];
    state.employees = employees;
  }
  return state.employees.map((employee) => ({
    id: employee.employee_id,
    name: employee.employee_id === state.me.employee_id
      ? (employee.display_name || employee.employee_id) + ' (me)'
      : employee.display_name || employee.employee_id,
  }));
}

async function loadLearning() {
  const generation = sessionGeneration;
  try {
    const select = $('#learnerSelect');
    const options = await learnerOptions();
    if (!isCurrent(generation)) return;
    if (!options.length) {
      $('#learningList').innerHTML = '<div class="empty">No learners available.</div>';
      return;
    }
    const previous = select.value;
    select.innerHTML = options.map((option) =>
      `<option value="${esc(option.id)}">${esc(option.name)}</option>`).join('');
    select.disabled = options.length === 1;
    if (previous && options.some((option) => option.id === previous)) select.value = previous;
    const learnerId = select.value || options[0].id;
    const page = await api(`/api/v1/assignments?employee_id=${encodeURIComponent(learnerId)}&limit=200`);
    if (!isCurrent(generation)) return;
    const items = page.items || [];
    if (!items.length) {
      $('#learningList').innerHTML = '<div class="empty">No assigned learning.</div>';
      return;
    }
    const self = state.me.employee_id === learnerId;
    const canStart = self || canStartAssignment();
    const canComplete = self || canCompleteAssignment();
    const canManage = can('manage_learning') || can('read_team');
    $('#learningList').innerHTML = items.map((item) => {
      const open = ['assigned', 'in_progress', 'failed'].includes(item.status);
      const actions = [];
      if (open) {
        actions.push(`<button type="button" data-action="open-course" data-course="${esc(item.course_code)}">Open course</button>`);
      } else if (item.status === 'completed') {
        actions.push(`<button type="button" class="secondary" data-action="open-course" data-course="${esc(item.course_code)}">Review course</button>`);
      }
      if (open && canStart && item.status === 'assigned') {
        actions.push(`<button type="button" class="secondary" data-action="start" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}">Start</button>`);
      }
      if (open && canComplete) {
        actions.push(`<button type="button" class="secondary" data-action="complete" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}" data-title="${esc(item.course_title)}">Complete</button>`);
      }
      if (open && canManage) {
        actions.push(`<button type="button" class="secondary" data-action="extend" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}">Extend</button>`);
        actions.push(`<button type="button" class="secondary" data-action="exempt" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}">Exempt</button>`);
      }
      if (item.status === 'completed' && can('manage_learning') && Number(item.recertification_days) > 0) {
        actions.push(`<button type="button" class="secondary" data-action="reassign" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}">Start recertification</button>`);
      }
      const evidence = item.status === 'completed'
        ? `<p class="muted">Completed ${esc((item.completed_at || '').slice(0, 10))}${item.score != null ? ` · score ${esc(item.score)}` : ''}${item.recert_due_date ? ` · recertify by ${esc(item.recert_due_date)}` : ''}</p>`
        : (item.status === 'failed' ? `<p class="muted">Last attempt ${esc(item.score ?? '')} — below the passing score.</p>` : '');
      const exempt = item.status === 'exempted'
        ? `<p class="muted">Exempted: ${esc(item.exemption_reason || 'no reason recorded')}</p>` : '';
      return `<article class="card"><h3>${esc(item.course_title)}</h3>` +
        `<small>${esc(item.course_code)} · ${esc(item.category)} · cycle ${esc(item.cycle)}</small>` +
        `<div class="meta">${statusTag(item.status)}<span class="tag">Due ${esc(item.due_date || '—')}</span>` +
        `<span class="tag">${esc(item.duration_minutes)} min</span>` +
        (item.recertification_days ? `<span class="tag">Recert ${esc(item.recertification_days)}d</span>` : '') +
        `</div>${evidence}${exempt}` +
        (actions.length ? `<div class="actions">${actions.join('')}</div>` : '') +
        `</article>`;
    }).join('');
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ------------------------------------------------------------------- team */

async function loadTeam() {
  const generation = sessionGeneration;
  try {
    const [team, assignments] = await Promise.all([
      api('/api/v1/team'),
      api('/api/v1/team/assignments?limit=200'),
    ]);
    if (!isCurrent(generation)) return;
    const body = $('#teamBody');
    let html = kpis([
      ['Team members', team.team_size],
      ['Assignments', team.total_assignments],
      ['Completed', team.completed_assignments],
      ['Completion rate', `${team.completion_rate}%`],
      ['Overdue', team.overdue_assignments],
    ]);
    html += '<div class="grid two">' + chart('Assignments by status', team.assignment_status) + '</div>';
    const rows = (assignments.items || []).map((item) => [
      `<strong>${esc(item.display_name)}</strong><br><small>${esc(item.employee_id)}</small>`,
      `<strong>${esc(item.course_title)}</strong><br><small>${esc(item.course_code)}</small>`,
      statusTag(item.status),
      esc(item.due_date || '—'),
      item.status === 'completed'
        ? `<span class="tag green">${esc((item.completed_at || '').slice(0, 10))}</span>`
        : `<button type="button" class="secondary" data-action="extend" data-employee="${esc(item.employee_id)}" data-course="${esc(item.course_code)}">Extend</button>`,
    ]);
    html += `<article><h2>Team assignments</h2>${table(['Member', 'Course', 'Status', 'Due', 'Action'], rows)}</article>`;
    body.innerHTML = html;
    hydrate(body);
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ----------------------------------------------------------------- people */

async function loadPeople(query = {}) {
  const generation = sessionGeneration;
  try {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value) params.set(key, value); });
    params.set('limit', '200');
    const page = await api('/api/v1/employees?' + params.toString());
    if (!isCurrent(generation)) return;
    state.employees = page.items;
    $('#peopleRows').innerHTML = page.items.map((employee) => `<tr>` +
      `<td><strong>${esc(employee.display_name)}</strong><br><small>${esc(employee.employee_id)} · ${esc(employee.work_email || '')}</small></td>` +
      `<td>${statusTag(employee.employment_status)}</td>` +
      `<td>${esc(employee.department || '—')}<br><small>${esc(employee.location || '')}</small></td>` +
      `<td>${esc(employee.job_title || '—')}<br><small>${esc(employee.job_code || '')}</small></td>` +
      `<td>${esc(employee.manager_employee_id || '—')}</td>` +
      `<td>${(employee.roles || []).map((role) => `<span class="tag">${esc(role.role)}</span>`).join(' ')}</td>` +
      `</tr>`).join('');
    $('#peopleMeta').textContent = `${page.total} employee(s) · showing ${page.items.length}`;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* --------------------------------------------------------------- policies */

async function loadPolicies() {
  const generation = sessionGeneration;
  try {
    const policies = await api('/api/v1/policies');
    if (!isCurrent(generation)) return;
    if (!state.courses.length) await loadCatalog();
    if (!isCurrent(generation)) return;
    $('#policyRows').innerHTML = policies.map((policy) => `<tr>` +
      `<td>${esc(policy.name)}</td>` +
      `<td>${esc(policy.course_code)}<br><small>${esc(policy.course_title)}</small></td>` +
      `<td><code>${esc(policy.attribute)} ${esc(policy.operator)} ${esc(policy.value)}</code></td>` +
      `<td>${esc(policy.due_days)} days</td>` +
      `<td>${statusTag(policy.active ? 'active' : 'inactive')}</td>` +
      `</tr>`).join('');
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ---------------------------------------------------------- notifications */

async function loadNotifications() {
  const generation = sessionGeneration;
  try {
    const page = await api('/api/v1/notifications?limit=100');
    const me = await api('/api/v1/me');
    if (!isCurrent(generation)) return;
    updateBell(me.unread_notifications || 0);
    const items = page.items || [];
    $('#notificationList').innerHTML = items.length ? items.map((item) => `<article class="card ${item.read_at ? 'muted-card' : ''}">` +
      `<div class="meta"><span class="tag">${esc(item.template)}</span>` +
      (item.read_at ? '<span class="tag green">Read</span>' : '<span class="tag amber">Unread</span>') +
      `<span class="tag">${esc((item.created_at || '').slice(0, 16).replace('T', ' '))}</span></div>` +
      `<h3>${esc(item.subject)}</h3><p class="prewrap">${esc(item.body)}</p>` +
      (item.read_at ? '' : `<div class="actions"><button type="button" class="secondary" data-action="mark-read" data-id="${esc(item.id)}">Mark read</button></div>`) +
      `</article>`).join('') : '<div class="empty">No notifications.</div>';
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ------------------------------------------------------------------- HRIS */

function importBody() {
  return {
    csv: state.csvText,
    filename: state.csvName,
    mode: $('#importMode').value,
    max_population_change_pct: Number($('#threshold').value),
  };
}

function invalidateImport(reason) {
  state.importSnapshot = null;
  $('#applyImport').disabled = true;
  if (reason) $('#importSummary').textContent = reason;
}

async function readCsv() {
  const generation = sessionGeneration;
  const file = $('#csvFile').files[0];
  if (!file) throw new Error('Select a CSV file');
  const csvText = await file.text();
  if (!isCurrent(generation)) return false;
  state.csvText = csvText;
  state.csvName = file.name;
  return true;
}

async function analyzeImport() {
  const generation = sessionGeneration;
  try {
    if (!await readCsv() || !isCurrent(generation)) return;
    const body = importBody();
    const result = await api('/api/v1/hris/analyze', { method: 'POST', body: JSON.stringify(body) });
    if (!isCurrent(generation)) return;
    const warnings = (result.warnings || []).length
      ? `<details><summary>Warnings</summary><code>${esc(JSON.stringify(result.warnings, null, 2))}</code></details>` : '';
    $('#importSummary').innerHTML =
      `<div class="meta">${result.can_apply ? '<span class="tag green">Ready to apply</span>' : '<span class="tag red">Blocked</span>'}` +
      `<span class="tag">${esc(result.row_count)} rows</span>` +
      `<span class="tag">${esc(result.creates.length)} creates</span>` +
      `<span class="tag">${esc(result.updates.length)} updates</span>` +
      `<span class="tag">${esc(result.suspends.length)} suspensions</span>` +
      `<span class="tag">${esc(result.rejected_count)} rejected</span></div>` +
      (result.blockers.length ? `<p><strong>Blockers:</strong> ${esc(result.blockers.join('; '))}</p>` : '') +
      (result.errors.length ? `<details><summary>Validation issues</summary><code>${esc(JSON.stringify(result.errors, null, 2))}</code></details>` : '') +
      warnings;
    $('#applyImport').disabled = !result.can_apply;
    state.importSnapshot = result.can_apply
      ? { body: JSON.stringify(body), summary: $('#importSummary').innerHTML }
      : null;
    toast('Dry run complete');
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function applyImport() {
  const generation = sessionGeneration;
  if (!state.importSnapshot) {
    toast('Analyze the current file before applying it', true);
    return;
  }
  const body = JSON.stringify(importBody());
  if (body !== state.importSnapshot.body) {
    invalidateImport('The extract or its options changed after analysis. Analyze it again.');
    toast('The reviewed import changed — analyze it again', true);
    return;
  }
  if (!window.confirm('Apply this employee extract? Account, role, and assignment changes will be audited.')) return;
  try {
    const result = await api('/api/v1/hris/apply', { method: 'POST', body });
    if (!isCurrent(generation)) return;
    toast(result.status === 'duplicate' ? 'This extract was already applied' : 'Import applied');
    $('#applyImport').disabled = true;
    await Promise.all([loadImportJobs(), loadDashboard()]);
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function loadImportJobs() {
  const generation = sessionGeneration;
  try {
    const jobs = await api('/api/v1/import-jobs');
    if (!isCurrent(generation)) return;
    $('#importRows').innerHTML = jobs.map((job) => `<tr>` +
      `<td>#${esc(job.id)}</td><td>${esc(job.filename)}</td><td>${statusTag(job.status)}</td>` +
      `<td>${esc(job.row_count)}</td><td>${esc(job.create_count)}</td>` +
      `<td>${esc(job.update_count)}</td><td>${esc(job.suspend_count)}</td></tr>`).join('');
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ audit */

async function loadAudit(query = {}) {
  const generation = sessionGeneration;
  try {
    const params = new URLSearchParams({ limit: '100' });
    Object.entries(query).forEach(([key, value]) => { if (value) params.set(key, value); });
    const page = await api('/api/v1/audit?' + params.toString());
    if (!isCurrent(generation)) return;
    $('#auditRows').innerHTML = page.items.map((event) => `<tr>` +
      `<td>${esc((event.created_at || '').slice(0, 19).replace('T', ' '))}</td>` +
      `<td>${esc(event.event_type)}</td><td>${esc(event.actor)}</td>` +
      `<td>${esc(event.entity_type)}<br><small>${esc(event.entity_id)}</small></td>` +
      `<td><code>${esc(JSON.stringify(event.details))}</code></td></tr>`).join('');
    $('#auditMeta').textContent = `${page.total} event(s) · showing ${page.items.length}`;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

/* ------------------------------------------------------------- engagement */

function badgeGlyph(icon) {
  return { star: '★', shield: '◆', trophy: '♛', certificate: '✜', bolt: '⚡', book: '▣' }[icon] || '★';
}

function pathCard(path) {
  const progress = path.my_progress || {};
  const actions = `<button type="button" data-action="open-path" data-path="${esc(path.id)}">View path</button>` +
    (progress.next_course
      ? `<button type="button" class="secondary" data-action="enroll-path" data-path="${esc(path.id)}">Start next course</button>`
      : '') +
    (canManagePaths()
      ? `<button type="button" class="icon danger" data-action="delete-path" data-path="${esc(path.id)}">Delete</button>`
      : '');
  return `<article class="card">
    <div class="meta">${statusTag(path.status)}<span class="tag">${esc(path.course_count)} courses</span>
      <span class="tag">${esc(path.content_minutes)} min</span></div>
    <h3>${esc(path.title)}</h3>
    <p>${esc(path.description || 'A guided sequence of courses.')}</p>
    <small>${esc(path.code)}</small>
    ${progress.required_courses ? progressBar(progress.percent, `${progress.completed_required}/${progress.required_courses} required`) : ''}
    <div class="actions">${actions}</div>
  </article>`;
}

async function loadPaths() {
  const generation = sessionGeneration;
  try {
    state.paths = await api('/api/v1/learning-paths');
    if (!isCurrent(generation)) return;
    const host = $('#pathCards');
    host.innerHTML = state.paths.length ? state.paths.map(pathCard).join('')
      : '<div class="empty">No learning paths are published yet.</div>';
    hydrate(host);
    $('#pathDetail').hidden = true;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function openPathDetail(pathId) {
  const generation = sessionGeneration;
  try {
    const path = await api('/api/v1/learning-paths/' + encodeURIComponent(pathId));
    if (!isCurrent(generation)) return;
    const steps = (path.courses || []).map((course, index) => {
      const assignment = course.assignment;
      const grade = course.grade || {};
      const done = assignment && ['completed', 'exempted'].includes(assignment.status);
      const action = assignment
        ? `<button type="button" class="small" data-action="open-course" data-course="${esc(course.course_code)}">Open course</button>`
        : `<button type="button" class="small" data-action="enroll-direct" data-course="${esc(course.course_code)}">Enroll</button>`;
      return `<li class="${done ? 'done' : ''}">
        <strong>${index + 1}. ${esc(course.title)}</strong><br>
        <small>${esc(course.course_code)} · ${esc(course.duration_minutes)} min · ${course.is_required ? 'Required' : 'Optional'}</small>
        <div class="meta">${assignment ? statusTag(assignment.status) : '<span class="tag">Not assigned</span>'}
          ${grade.score !== null && grade.score !== undefined ? `<span class="tag">${esc(grade.score)}% · ${esc(grade.letter)}</span>` : ''}</div>
        <div class="step-actions">${action}
          ${canManagePaths() ? `<button type="button" class="icon danger" data-action="remove-path-course" data-path="${esc(path.id)}" data-course="${esc(course.course_code)}">Remove</button>` : ''}
        </div>
      </li>`;
    }).join('') || '<li class="muted small">No courses in this path.</li>';
    let addCourse = '';
    if (canManagePaths()) {
      if (!state.courses.length) await loadCatalog();
      if (!isCurrent(generation)) return;
      addCourse = `<form class="inline-form" data-form="pathCourse">
        <input type="hidden" name="path_id" value="${esc(path.id)}">
        <label>Course<select name="course_code" required>${state.courses.map((item) =>
          `<option value="${esc(item.course_code)}">${esc(item.course_code)} — ${esc(item.title)}</option>`).join('')}</select></label>
        <button type="submit">Add course</button>
      </form>`;
    }
    const detail = $('#pathDetail');
    detail.innerHTML = `<article class="card">
      <div class="meta">${statusTag(path.status)}<span class="tag">${esc(path.code)}</span></div>
      <h2>${esc(path.title)}</h2><p>${esc(path.description || '')}</p>
      ${path.progress ? progressBar(path.progress.percent, `${path.progress.completed_required}/${path.progress.required_courses} required`) : ''}
      <ol class="path-steps">${steps}</ol>${addCourse}
      <div class="detail-actions"><button type="button" class="secondary" data-action="refresh-paths">Back to paths</button></div>
    </article>`;
    detail.hidden = false;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function loadGradebook() {
  const generation = sessionGeneration;
  try {
    const options = await learnerOptions();
    if (!isCurrent(generation)) return;
    const select = $('#gradebookLearner');
    const previous = select.value;
    select.innerHTML = options.map((item) =>
      `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
    select.disabled = options.length === 1;
    if (previous && options.some((item) => item.id === previous)) select.value = previous;
    const learnerId = select.value || options[0].id;
    const data = await api('/api/v1/gradebook?employee_id=' + encodeURIComponent(learnerId));
    if (!isCurrent(generation)) return;
    state.gradebook = data;
    const gradeRows = (data.grades || []).map((grade) => [
      `<strong>${esc(grade.course_title)}</strong><br><small>${esc(grade.course_code)}</small>`,
      statusTag(grade.status),
      `<span class="grade-score">${grade.score === null ? '—' : esc(grade.score) + '%'}</span> <span class="tag">${esc(grade.letter)}</span>`,
      `${esc(grade.components.length)} quiz component(s)`,
      esc(grade.source),
    ]);
    const grades = table(['Course', 'Status', 'Final grade', 'Evidence', 'Source'], gradeRows);
    const badgeRows = (data.badges || []).map((badge) => [
      `<span class="badge-icon">${esc(badgeGlyph(badge.icon))}</span><strong>${esc(badge.title)}</strong>`,
      esc(badge.source),
      esc((badge.awarded_at || '').slice(0, 16).replace('T', ' ')),
      `<code class="credential-code">${esc(badge.verification_code)}</code>`,
    ]);
    const badges = table(['Credential', 'Source', 'Awarded', 'Verification code'], badgeRows);
    const pathRows = (data.paths || []).map((path) => [
      esc(path.title), esc(path.course_count), progressBar(path.my_progress?.percent || 0, `${path.my_progress?.completed_required || 0}/${path.my_progress?.required_courses || 0}`),
      path.my_progress?.next_course ? `<span class="tag amber">${esc(path.my_progress.next_course)}</span>` : '<span class="tag green">Complete</span>',
    ]);
    $('#gradebookBody').innerHTML = `<article><h2>Course grades</h2>${grades}</article>
      <article><h2>Earned credentials</h2>${badges}</article>
      <article><h2>Learning paths</h2>${table(['Path', 'Courses', 'Progress', 'Next'], pathRows)}</article>`;
    $('#gradebookMeta').textContent = `${data.grades.length} course grade(s) · ${data.badges.length} credential(s)`;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function loadCredentials() {
  const generation = sessionGeneration;
  try {
    const suffix = can('manage_learning') ? '?include_inactive=true' : '';
    const [badges, earned] = await Promise.all([
      api('/api/v1/badges' + suffix),
      api('/api/v1/credentials/earned'),
    ]);
    if (!isCurrent(generation)) return;
    state.badges = badges;
    if (!state.courses.length) await loadCatalog();
    if (!isCurrent(generation)) return;
    const paths = await api('/api/v1/learning-paths');
    if (!isCurrent(generation)) return;
    const courseSelect = $('#badgeDialog form [name=criteria_course_code]');
    const pathSelect = $('#badgeDialog form [name=criteria_path_id]');
    courseSelect.innerHTML = '<option value="">Manual award</option>' + state.courses.map((course) =>
      `<option value="${esc(course.course_code)}">${esc(course.course_code)} — ${esc(course.title)}</option>`).join('');
    pathSelect.innerHTML = '<option value="">None</option>' + paths.map((path) =>
      `<option value="${esc(path.id)}">${esc(path.title)}</option>`).join('');

    const host = $('#credentialCards');
    host.innerHTML = badges.map((badge) => {
      const award = earned.find((item) => Number(item.badge_id) === Number(badge.id));
      const awardForm = can('manage_learning')
        ? `<form class="inline-form" data-form="award"><input type="hidden" name="badge_id" value="${esc(badge.id)}">
             <label>Award to<input name="employee_id" placeholder="E1003" required></label>
             <button type="submit">Award manually</button></form>`
        : '';
      return `<article class="card">
        <div class="meta"><span class="badge-icon">${esc(badgeGlyph(badge.icon))}</span>
          <span class="tag">${esc(badge.criteria_title)}</span>
          ${award ? '<span class="tag green">Earned</span>' : ''}</div>
        <h3>${esc(badge.title)}</h3><p>${esc(badge.description || '')}</p>
        <small>${esc(badge.code)} · ${esc(badge.award_count)} awarded</small>
        ${award ? `<p><code class="credential-code">${esc(award.verification_code)}</code></p>` : ''}
        ${awardForm}
      </article>`;
    }).join('') || '<div class="empty">No badges have been defined yet.</div>';
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function loadCohorts() {
  const generation = sessionGeneration;
  try {
    state.cohorts = await api('/api/v1/cohorts');
    if (!isCurrent(generation)) return;
    $('#cohortCards').innerHTML = state.cohorts.length ? state.cohorts.map((cohort) =>
      `<article class="card">
        <div class="meta">${statusTag(cohort.active ? 'active' : 'inactive')}
          <span class="tag">${esc(cohort.member_count)} members</span></div>
        <h3>${esc(cohort.name)}</h3><p>${esc(cohort.description || '')}</p>
        <small>${esc(cohort.code)}</small>
        <div class="actions"><button type="button" data-action="open-cohort" data-cohort="${esc(cohort.id)}">Manage members</button></div>
      </article>`).join('') : '<div class="empty">No cohorts yet.</div>';
    $('#cohortDetail').hidden = true;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function openCohortDetail(cohortId) {
  const generation = sessionGeneration;
  try {
    const cohort = await api('/api/v1/cohorts/' + encodeURIComponent(cohortId));
    if (!isCurrent(generation)) return;
    if (!state.employees.length) {
      const employees = await api('/api/v1/learners');
      if (!isCurrent(generation)) return;
      state.employees = employees;
    }
    const members = table(['Employee', 'Department', 'Status', 'Added by', ''],
      (cohort.members || []).map((member) => [
        `<strong>${esc(member.display_name)}</strong><br><small>${esc(member.employee_id)}</small>`,
        esc(member.department || '—'), statusTag(member.employment_status || 'active'),
        esc(member.added_by || '—'),
        `<button type="button" class="icon danger" data-action="remove-cohort-member" data-cohort="${esc(cohort.id)}" data-employee="${esc(member.employee_id)}">Remove</button>`,
      ]));
    const detail = $('#cohortDetail');
    detail.innerHTML = `<article class="card">
      <div class="meta">${statusTag(cohort.active ? 'active' : 'inactive')}<span class="tag">${esc(cohort.code)}</span></div>
      <h2>${esc(cohort.name)}</h2><p>${esc(cohort.description || '')}</p>
      ${members}
      <form class="inline-form" data-form="cohortMember">
        <input type="hidden" name="cohort_id" value="${esc(cohort.id)}">
        <label>Employee<select name="employee_id" required>${state.employees.map((employee) =>
          `<option value="${esc(employee.employee_id)}">${esc(employee.display_name)} · ${esc(employee.employee_id)}</option>`).join('')}</select></label>
        <button type="submit">Add member</button>
      </form>
      <div class="detail-actions"><button type="button" class="secondary" data-action="refresh-cohorts">Back to cohorts</button></div>
    </article>`;
    detail.hidden = false;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function loadDiscussions() {
  const generation = sessionGeneration;
  try {
    if (!state.courses.length) {
      await loadCatalog();
      if (!isCurrent(generation)) return;
    }
    let courses = state.courses;
    if (!(can('manage_content') || can('manage_learning'))) {
      const page = await api('/api/v1/assignments?employee_id=' +
        encodeURIComponent(state.me.employee_id) + '&limit=200');
      if (!isCurrent(generation)) return;
      const enrolled = new Set((page.items || []).map((item) => item.course_code));
      courses = courses.filter((course) => enrolled.has(course.course_code));
    }
    const select = $('#discussionCourse');
    const previous = select.value;
    select.innerHTML = courses.map((course) =>
      `<option value="${esc(course.course_code)}">${esc(course.course_code)} — ${esc(course.title)}</option>`).join('');
    const newButton = $('#newDiscussion');
    if (newButton) newButton.hidden = !courses.length;
    if (!courses.length) {
      $('#discussionList').innerHTML = '<div class="empty">No course discussions available.</div>';
      $('#discussionDetail').hidden = true;
      return;
    }
    if (previous && courses.some((course) => course.course_code === previous)) select.value = previous;
    const code = select.value || courses[0].course_code;
    state.discussions = await api('/api/v1/courses/' + encodeURIComponent(code) + '/discussions');
    if (!isCurrent(generation)) return;
    $('#discussionList').innerHTML = state.discussions.length ? state.discussions.map((item) =>
      `<article class="card">
        <div class="meta">${item.pinned ? '<span class="tag amber">Pinned</span>' : ''}
          <span class="tag">${esc(item.post_count)} posts</span>
          <span class="tag">${esc(item.author_name || item.created_by)}</span></div>
        <h3>${esc(item.title)}</h3><p class="prewrap">${esc(item.body || '')}</p>
        <small>${esc((item.last_post_at || item.updated_at || '').slice(0, 16).replace('T', ' '))}</small>
        <div class="actions"><button type="button" data-action="open-discussion" data-discussion="${esc(item.id)}">Open thread</button></div>
      </article>`).join('') : '<div class="empty">No discussions for this course yet.</div>';
    $('#discussionDetail').hidden = true;
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

async function openDiscussionDetail(discussionId) {
  const generation = sessionGeneration;
  try {
    const item = await api('/api/v1/discussions/' + encodeURIComponent(discussionId));
    if (!isCurrent(generation)) return;
    const posts = (item.posts || []).map((post) => `<div class="discussion-post">
      <div class="post-head"><strong>${esc(post.author_name || post.author_id)}</strong>
        <small>${esc((post.created_at || '').slice(0, 16).replace('T', ' '))}</small>
        ${post.edited_at ? '<span class="tag">Edited</span>' : ''}</div>
      <p class="prewrap">${esc(post.body)}</p>
      <div class="detail-actions">
        <button type="button" class="small" data-action="reply-discussion" data-discussion="${esc(item.id)}" data-parent="${esc(post.id)}">Reply</button>
        ${can('manage_content') || post.author_id === state.me.employee_id
          ? `<button type="button" class="icon danger" data-action="delete-post" data-discussion="${esc(item.id)}" data-post="${esc(post.id)}">Delete</button>` : ''}
      </div>
    </div>`).join('') || '<div class="empty">No replies yet.</div>';
    const detail = $('#discussionDetail');
    detail.innerHTML = `<article class="card">
      <div class="meta">${item.pinned ? '<span class="tag amber">Pinned</span>' : ''}
        ${item.closed ? '<span class="tag red">Closed</span>' : ''}
        <span class="tag">${esc(item.course_code)}</span></div>
      <h2>${esc(item.title)}</h2><p class="prewrap">${esc(item.body || '')}</p>
      ${posts}
      ${item.closed ? '' : `<div class="detail-actions"><button type="button" data-action="reply-discussion" data-discussion="${esc(item.id)}">Add a reply</button></div>`}
      ${can('manage_content') || item.created_by === state.me.employee_id
        ? `<div class="detail-actions"><button type="button" class="icon danger" data-action="delete-discussion" data-discussion="${esc(item.id)}">Delete discussion</button></div>` : ''}
    </article>`;
    detail.hidden = false;
    detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    if (isCurrent(generation)) toast(error.message, true);
  }
}

function openCohortDialog(cohort = null) {
  const form = $('#cohortDialog form');
  form.reset();
  form.cohort_id.value = cohort ? cohort.id : '';
  $('#cohortDialogTitle').textContent = cohort ? 'Edit cohort' : 'New cohort';
  if (cohort) {
    setForm(form, { code: cohort.code, name: cohort.name, description: cohort.description, active: cohort.active });
    form.employees.value = '';
  }
  openDialog('cohortDialog');
}

function openPathDialog(path = null) {
  const form = $('#pathDialog form');
  form.reset();
  form.path_id.value = path ? path.id : '';
  $('#pathDialogTitle').textContent = path ? 'Edit path' : 'New learning path';
  if (path) setForm(form, {
    code: path.code, title: path.title, description: path.description, status: path.status,
    courses: (path.courses || []).map((course) => course.course_code).join('\n'),
  });
  openDialog('pathDialog');
}

function openBadgeDialog(badge = null) {
  const form = $('#badgeDialog form');
  form.reset();
  form.badge_id.value = badge ? badge.id : '';
  $('#badgeDialogTitle').textContent = badge ? 'Edit badge' : 'New badge';
  if (badge) setForm(form, {
    code: badge.code, title: badge.title, description: badge.description, icon: badge.icon,
    criteria_course_code: badge.criteria_course_code || '',
    criteria_path_id: badge.criteria_path_id || '', active: badge.active,
  });
  openDialog('badgeDialog');
}

/* ------------------------------------------------------------------ views */

const VIEW_LOADERS = {
  dashboard: loadDashboard,
  catalog: () => loadCatalog(),
  course: () => (state.course ? openCourse(state.course.course_code, state.lessonId) : showView('catalog')),
  learning: loadLearning,
  paths: loadPaths,
  gradebook: loadGradebook,
  credentials: loadCredentials,
  cohorts: loadCohorts,
  discussions: loadDiscussions,
  team: loadTeam,
  people: () => loadPeople(),
  policies: loadPolicies,
  notifications: loadNotifications,
  hris: loadImportJobs,
  audit: () => loadAudit(),
};

function activateView(name, push = true) {
  state.view = name;
  $$('nav button').forEach((item) => {
    const active = item.dataset.view === name;
    item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
  $$('.view').forEach((section) => section.classList.toggle('active', section.id === name));
  if (push) {
    const hash = name === 'course' && state.course
      ? '#/course/' + encodeURIComponent(state.course.course_code)
      : '#/' + name;
    if (location.hash !== hash) history.pushState(null, '', hash);
    lastRouteHash = hash;
  }
}

function showView(name, push = true) {
  const button = $(`nav button[data-view="${name}"]`);
  if (button && button.hidden) {
    name = 'dashboard';
    push = true;
  }
  activateView(name, push);
  return loadView(name);
}

async function loadView(name) {
  const generation = sessionGeneration;
  const loader = VIEW_LOADERS[name];
  if (loader) await loader();
  if (!isCurrent(generation)) return;
}

function routeFromHash(force = false) {
  const hash = location.hash;
  if (!force && hash === lastRouteHash) return;
  lastRouteHash = hash;
  const raw = hash.replace(/^#\/?/, '');
  if (!raw || raw === 'main') return;
  const [kind, value] = raw.split('/');
  if (kind === 'course' && value) {
    openCourse(decodeURIComponent(value));
    return;
  }
  const button = $(`nav button[data-view="${kind}"]`);
  const target = button && !button.hidden ? kind : 'dashboard';
  lastRouteHash = '#/' + target;
  showView(target, false);
}

window.addEventListener('popstate', () => routeFromHash(true));
window.addEventListener('hashchange', () => routeFromHash(true));

/* ---------------------------------------------------------------- actions */

function openDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && !dialog.open) dialog.showModal();
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && dialog.open) dialog.close();
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form));
  $$('input[type=checkbox]', form).forEach((box) => { data[box.name] = box.checked; });
  return data;
}

function assignmentDialog(id, employeeId, courseCode, title) {
  const form = $('#' + id + ' form');
  form.employee_id.value = employeeId;
  form.course_code.value = courseCode;
  if (title && id === 'completeDialog') {
    $('#completeSummary').textContent = `${title} (${courseCode}) for ${employeeId}`;
  }
  openDialog(id);
}

async function exportCsv() {
  try {
    const response = await rawFetch('/api/v1/reports/compliance.csv');
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.message || 'Export failed');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'compliance.csv';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast('Compliance export downloaded');
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener('click', async (event) => {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === 'refresh-session') {
      await refreshSession();
      toast('Session switched');
    } else if (action === 'show-notifications') {
      await showView('notifications');
    } else if (action === 'open-dialog') {
      const dialog = target.dataset.dialog;
      if (dialog === 'cohortDialog') openCohortDialog(null);
      else if (dialog === 'pathDialog') openPathDialog(null);
      else if (dialog === 'badgeDialog') openBadgeDialog(null);
      else if (dialog === 'discussionDialog') {
        const form = $('#discussionDialog form');
        form.reset();
        form.course_code.value = $('#discussionCourse').value || '';
        openDialog('discussionDialog');
      } else openDialog(dialog);
    } else if (action === 'close-dialog') {
      closeDialog(target.dataset.dialog);
    } else if (action === 'refresh-dashboard') {
      await loadDashboard();
    } else if (action === 'refresh-team') {
      await loadTeam();
    } else if (action === 'refresh-audit') {
      await loadAudit();
    } else if (action === 'export-csv') {
      await exportCsv();
    } else if (action === 'apply-policies') {
      const result = await api('/api/v1/policies/apply', { method: 'POST', body: '{}' });
      toast(`${result.assignments_created} assignment(s) created`);
      await loadPolicies();
    } else if (action === 'open-course') {
      await openCourse(target.dataset.course);
    } else if (action === 'back-catalog') {
      await showView('catalog');
    } else if (action === 'select-lesson') {
      state.lessonId = Number(target.dataset.lesson);
      renderCourse();
      await renderPlayer();
    } else if (action === 'complete-lesson') {
      await completeLesson(target.dataset.lesson);
    } else if (action === 'enroll') {
      await api(`/api/v1/courses/${encodeURIComponent(target.dataset.course)}/enroll`, {
        method: 'POST', body: '{}',
      });
      toast('Enrolled — the course is now in your learning');
      await refreshCourse();
    } else if (action === 'new-course') {
      openCourseDialog(null);
    } else if (action === 'edit-course') {
      openCourseDialog(state.course);
    } else if (action === 'new-module') {
      openModuleDialog(null);
    } else if (action === 'edit-module') {
      const module = (state.course.modules || []).find((item) => String(item.id) === target.dataset.module);
      if (module) openModuleDialog(module);
    } else if (action === 'delete-module') {
      if (!window.confirm('Delete this module and all of its lessons?')) return;
      await api(`/api/v1/modules/${target.dataset.module}`, { method: 'DELETE', body: '{}' });
      toast('Module deleted');
      await refreshCourse();
    } else if (action === 'new-lesson') {
      openLessonDialog(Number(target.dataset.module), null);
    } else if (action === 'edit-lesson') {
      const found = findLesson(state.course, target.dataset.lesson);
      if (found) openLessonDialog(found.module.id, found.lesson);
    } else if (action === 'delete-lesson') {
      if (!window.confirm('Delete this lesson and its progress?')) return;
      await api(`/api/v1/lessons/${target.dataset.lesson}`, { method: 'DELETE', body: '{}' });
      toast('Lesson deleted');
      if (String(state.lessonId) === target.dataset.lesson) state.lessonId = null;
      const course = await refreshCourse(false);
      state.lessonId = state.lessonId || defaultLessonId(course);
      renderCourse();
      await renderPlayer();
    } else if (action === 'manage-questions') {
      await openQuestionManager(Number(target.dataset.lesson));
    } else if (action === 'new-question') {
      openQuestionDialog(null);
    } else if (action === 'edit-question') {
      const question = state.questionCache.find((item) => String(item.id) === target.dataset.question);
      if (question) openQuestionDialog(question);
    } else if (action === 'delete-question') {
      if (!window.confirm('Delete this question?')) return;
      await api(`/api/v1/questions/${target.dataset.question}`, { method: 'DELETE', body: '{}' });
      toast('Question deleted');
      await refreshCourse(false);
      await openQuestionManager(state.questionLesson);
    } else if (action === 'refresh-paths') {
      await loadPaths();
    } else if (action === 'open-path') {
      await openPathDetail(target.dataset.path);
    } else if (action === 'enroll-path') {
      await api(`/api/v1/learning-paths/${target.dataset.path}/enroll`, {
        method: 'POST', body: '{}',
      });
      toast('Next course added to your learning');
      await openPathDetail(target.dataset.path);
    } else if (action === 'enroll-direct') {
      await api(`/api/v1/courses/${encodeURIComponent(target.dataset.course)}/enroll`, {
        method: 'POST', body: '{}',
      });
      toast('Enrolled in course');
      await loadPaths();
      await loadGradebook();
    } else if (action === 'delete-path') {
      if (!window.confirm('Delete this learning path?')) return;
      await api(`/api/v1/learning-paths/${target.dataset.path}`, { method: 'DELETE', body: '{}' });
      toast('Learning path deleted');
      await loadPaths();
    } else if (action === 'remove-path-course') {
      await api(`/api/v1/learning-paths/${target.dataset.path}/courses/${encodeURIComponent(target.dataset.course)}`, {
        method: 'DELETE', body: '{}',
      });
      toast('Course removed from path');
      await openPathDetail(target.dataset.path);
    } else if (action === 'refresh-cohorts') {
      await loadCohorts();
    } else if (action === 'open-cohort') {
      await openCohortDetail(target.dataset.cohort);
    } else if (action === 'remove-cohort-member') {
      await api(`/api/v1/cohorts/${target.dataset.cohort}/members/${encodeURIComponent(target.dataset.employee)}`, {
        method: 'DELETE', body: '{}',
      });
      toast('Member removed');
      await openCohortDetail(target.dataset.cohort);
    } else if (action === 'delete-cohort') {
      if (!window.confirm('Delete this cohort?')) return;
      await api(`/api/v1/cohorts/${target.dataset.cohort}`, { method: 'DELETE', body: '{}' });
      toast('Cohort deleted');
      await loadCohorts();
    } else if (action === 'open-discussion') {
      await openDiscussionDetail(target.dataset.discussion);
    } else if (action === 'reply-discussion') {
      const form = $('#postDialog form');
      form.reset();
      form.discussion_id.value = target.dataset.discussion;
      form.parent_id.value = target.dataset.parent || '';
      $('#postDialogTitle').textContent = target.dataset.parent ? 'Reply to post' : 'Add a reply';
      openDialog('postDialog');
    } else if (action === 'delete-discussion') {
      if (!window.confirm('Delete this discussion and its replies?')) return;
      await api(`/api/v1/discussions/${target.dataset.discussion}`, { method: 'DELETE', body: '{}' });
      toast('Discussion deleted');
      await loadDiscussions();
    } else if (action === 'delete-post') {
      if (!window.confirm('Delete this reply?')) return;
      await api(`/api/v1/discussions/${target.dataset.discussion}/posts/${target.dataset.post}`, {
        method: 'DELETE', body: '{}',
      });
      toast('Reply deleted');
      await openDiscussionDetail(target.dataset.discussion);
    } else if (action === 'start') {
      await api('/api/v1/assignments/start', {
        method: 'POST',
        body: JSON.stringify({ employee_id: target.dataset.employee, course_code: target.dataset.course }),
      });
      toast('Learning started');
      await loadLearning();
    } else if (action === 'complete') {
      assignmentDialog('completeDialog', target.dataset.employee, target.dataset.course, target.dataset.title);
    } else if (action === 'exempt') {
      assignmentDialog('exemptDialog', target.dataset.employee, target.dataset.course);
    } else if (action === 'extend') {
      assignmentDialog('extendDialog', target.dataset.employee, target.dataset.course);
    } else if (action === 'reassign') {
      await api('/api/v1/assignments/reassign', {
        method: 'POST',
        body: JSON.stringify({ employee_id: target.dataset.employee, course_code: target.dataset.course, due_days: 30 }),
      });
      toast('Recertification assignment created');
      await loadLearning();
    } else if (action === 'mark-read') {
      const result = await api(`/api/v1/notifications/${target.dataset.id}/read`, { method: 'POST', body: '{}' });
      updateBell(result.unread);
      await loadNotifications();
    } else if (action === 'mark-all-read') {
      await api('/api/v1/notifications/read-all', { method: 'POST', body: '{}' });
      updateBell(0);
      await loadNotifications();
    } else if (action === 'sweep-notifications') {
      const result = await api('/api/v1/notifications/sweep', { method: 'POST', body: '{}' });
      toast(`Queued: ${result.due_soon} due soon, ${result.overdue} overdue, ${result.recertification} recertification`);
      await loadNotifications();
    } else if (action === 'analyze-import') {
      await analyzeImport();
    } else if (action === 'apply-import') {
      await applyImport();
    }
  } catch (error) {
    toast(error.message, true);
  }
});

document.addEventListener('change', async (event) => {
  const target = event.target;
  try {
    if (target.id === 'courseThumbFile') {
      const file = target.files[0];
      if (!file) return;
      const item = await uploadMedia(file);
      const form = $('#courseDialog form');
      form.thumbnail_media_id.value = item.id;
      form.thumbnail_url.value = '';
      $('#courseThumbName').textContent = `Uploaded ${item.filename}`;
      toast('Cover image uploaded');
    } else if (target.id === 'lessonVideoFile') {
      const file = target.files[0];
      if (!file) return;
      const item = await uploadMedia(file);
      const form = $('#lessonDialog form');
      form.media_id.value = item.id;
      form.url_content.value = '';
      $('#lessonVideoName').textContent = `Uploaded ${item.filename}`;
      toast('Video uploaded');
    } else if (target.id === 'lessonType') {
      syncLessonFields();
    } else if (target.id === 'csvFile') {
      invalidateImport('The selected file changed. Analyze it again before applying.');
    } else if (target.id === 'importMode' || target.id === 'threshold') {
      invalidateImport('The import options changed. Analyze it again before applying.');
    }
  } catch (error) {
    toast(error.message, true);
  }
});

document.addEventListener('submit', async (event) => {
  const form = event.target;
  const kind = form.dataset.form;
  if (!kind) return;
  event.preventDefault();
  try {
    if (kind === 'employee') {
      await api('/api/v1/employees', { method: 'POST', body: JSON.stringify(formData(form)) });
      form.reset();
      closeDialog('employeeDialog');
      toast('Employee created');
      await loadPeople();
    } else if (kind === 'course') {
      const data = formData(form);
      const original = data.original_code;
      const payload = {
        course_code: data.original_code || data.course_code,
        title: data.title, category: data.category,
        level: data.level, language: data.language, author: data.author,
        duration_minutes: Number(data.duration_minutes || 30),
        version: data.version,
        recertification_days: Number(data.recertification_days || 0),
        passing_score: data.passing_score === '' ? null : Number(data.passing_score),
        status: data.status, mandatory: Boolean(data.mandatory),
        summary: data.summary, description: data.description,
        objectives: data.objectives, tags: data.tags,
        prerequisite_codes: data.prerequisite_codes,
        thumbnail_url: data.thumbnail_url || '',
        content_url: data.content_url || '',
      };
      if (data.thumbnail_media_id) payload.thumbnail_media_id = Number(data.thumbnail_media_id);
      if (original) {
        await api('/api/v1/courses/' + encodeURIComponent(original), {
          method: 'PATCH', body: JSON.stringify(payload),
        });
      } else {
        await api('/api/v1/courses', { method: 'POST', body: JSON.stringify(payload) });
      }
      closeDialog('courseDialog');
      toast(original ? 'Course updated' : 'Course created');
      await loadCatalog();
      if (original && state.course && state.course.course_code === original) {
        await openCourse(original, state.lessonId);
      }
    } else if (kind === 'module') {
      const data = formData(form);
      const payload = { title: data.title, summary: data.summary };
      if (data.module_id) {
        await api('/api/v1/modules/' + data.module_id, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api(`/api/v1/courses/${encodeURIComponent(data.course_code)}/modules`, {
          method: 'POST', body: JSON.stringify(payload),
        });
      }
      closeDialog('moduleDialog');
      toast(data.module_id ? 'Module updated' : 'Module added');
      await refreshCourse();
    } else if (kind === 'lesson') {
      const data = formData(form);
      const type = data.lesson_type;
      const payload = {
        title: data.title,
        lesson_type: type,
        duration_minutes: Number(data.duration_minutes || 0),
        is_required: Boolean(data.is_required),
        quiz_pass_score: data.quiz_pass_score === '' ? 70 : Number(data.quiz_pass_score),
      };
      if (type === 'text' || type === 'quiz') payload.content = data.content;
      else if (type === 'youtube' || type === 'link') payload.content = data.url_content;
      else if (type === 'video') {
        if (data.media_id) payload.media_id = Number(data.media_id);
        if (data.url_content) payload.content = data.url_content;
      }
      let saved;
      if (data.lesson_id) {
        saved = await api('/api/v1/lessons/' + data.lesson_id, {
          method: 'PATCH', body: JSON.stringify(payload),
        });
      } else {
        saved = await api(`/api/v1/modules/${data.module_id}/lessons`, {
          method: 'POST', body: JSON.stringify(payload),
        });
      }
      closeDialog('lessonDialog');
      toast(data.lesson_id ? 'Lesson updated' : 'Lesson added');
      await refreshCourse(false);
      if (!data.lesson_id && saved && saved.id) state.lessonId = saved.id;
      renderCourse();
      await renderPlayer();
      if (type === 'quiz' && !data.lesson_id && saved && saved.id) {
        await openQuestionManager(saved.id);
      }
    } else if (kind === 'question') {
      const data = formData(form);
      const options = String(data.options || '').split('\n').map((line) => line.trim()).filter(Boolean);
      const payload = {
        prompt: data.prompt,
        options,
        correct_index: Number(data.correct_index || 1) - 1,
        explanation: data.explanation,
      };
      if (data.question_id) {
        await api('/api/v1/questions/' + data.question_id, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api(`/api/v1/lessons/${data.lesson_id}/questions`, { method: 'POST', body: JSON.stringify(payload) });
      }
      closeDialog('questionDialog');
      toast(data.question_id ? 'Question updated' : 'Question added');
      await refreshCourse(false);
      await openQuestionManager(Number(data.lesson_id));
    } else if (kind === 'quiz') {
      const answers = $$('.quiz-q', form).map((fieldset) => {
        const checked = $('input[type=radio]:checked', fieldset);
        return checked ? Number(checked.value) : -1;
      });
      const lessonId = Number(form.dataset.lesson);
      const result = await api(`/api/v1/lessons/${lessonId}/quiz/attempt`, {
        method: 'POST', body: JSON.stringify({ answers }),
      });
      await refreshCourse(false);
      renderCourse();
      renderQuizResult(lessonId, result);
      toast(result.passed ? `Passed with ${result.score}%` : `Scored ${result.score}% — try again`, !result.passed);
    } else if (kind === 'policy') {
      const data = formData(form);
      data.due_days = Number(data.due_days || 30);
      await api('/api/v1/policies', { method: 'POST', body: JSON.stringify(data) });
      closeDialog('policyDialog');
      toast('Policy created');
      await loadPolicies();
    } else if (kind === 'complete') {
      const data = formData(form);
      const payload = { employee_id: data.employee_id, course_code: data.course_code };
      if (data.score !== '') payload.score = Number(data.score);
      await api('/api/v1/assignments/complete', { method: 'POST', body: JSON.stringify(payload) });
      closeDialog('completeDialog');
      toast('Completion recorded');
      await loadLearning();
    } else if (kind === 'exempt') {
      const data = formData(form);
      await api('/api/v1/assignments/exempt', { method: 'POST', body: JSON.stringify(data) });
      closeDialog('exemptDialog');
      toast('Exemption recorded');
      await loadLearning();
    } else if (kind === 'extend') {
      const data = formData(form);
      await api('/api/v1/assignments/extend', { method: 'POST', body: JSON.stringify(data) });
      closeDialog('extendDialog');
      toast('Due date extended');
      await loadLearning();
    } else if (kind === 'cohort') {
      const data = formData(form);
      const payload = {
        code: data.code, name: data.name, description: data.description,
        active: Boolean(data.active),
        employees: String(data.employees || '').split('\n').map((line) => line.trim()).filter(Boolean),
      };
      if (data.cohort_id) {
        await api('/api/v1/cohorts/' + data.cohort_id, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api('/api/v1/cohorts', { method: 'POST', body: JSON.stringify(payload) });
      }
      closeDialog('cohortDialog');
      toast('Cohort saved');
      await loadCohorts();
    } else if (kind === 'path') {
      const data = formData(form);
      const payload = {
        code: data.code, title: data.title, description: data.description,
        status: data.status,
        courses: String(data.courses || '').split('\n').map((line) => line.trim()).filter(Boolean),
      };
      if (data.path_id) {
        await api('/api/v1/learning-paths/' + data.path_id, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api('/api/v1/learning-paths', { method: 'POST', body: JSON.stringify(payload) });
      }
      closeDialog('pathDialog');
      toast('Learning path saved');
      await loadPaths();
    } else if (kind === 'badge') {
      const data = formData(form);
      const payload = {
        code: data.code, title: data.title, description: data.description,
        icon: data.icon, criteria_course_code: data.criteria_course_code,
        criteria_path_id: data.criteria_path_id, active: Boolean(data.active),
      };
      if (data.badge_id) {
        await api('/api/v1/badges/' + data.badge_id, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await api('/api/v1/badges', { method: 'POST', body: JSON.stringify(payload) });
      }
      closeDialog('badgeDialog');
      toast('Badge saved');
      await loadCredentials();
    } else if (kind === 'discussion') {
      const data = formData(form);
      await api('/api/v1/courses/' + encodeURIComponent(data.course_code) + '/discussions', {
        method: 'POST', body: JSON.stringify({ title: data.title, body: data.body }),
      });
      closeDialog('discussionDialog');
      toast('Discussion created');
      await loadDiscussions();
    } else if (kind === 'post') {
      const data = formData(form);
      const payload = { body: data.body };
      if (data.parent_id) payload.parent_id = Number(data.parent_id);
      await api('/api/v1/discussions/' + data.discussion_id + '/posts', {
        method: 'POST', body: JSON.stringify(payload),
      });
      closeDialog('postDialog');
      toast('Reply posted');
      await openDiscussionDetail(data.discussion_id);
    } else if (kind === 'cohortMember') {
      const data = formData(form);
      await api('/api/v1/cohorts/' + data.cohort_id + '/members', {
        method: 'POST', body: JSON.stringify({ employee_id: data.employee_id }),
      });
      toast('Member added');
      await openCohortDetail(data.cohort_id);
    } else if (kind === 'pathCourse') {
      const data = formData(form);
      await api('/api/v1/learning-paths/' + data.path_id + '/courses', {
        method: 'POST', body: JSON.stringify({ course_code: data.course_code }),
      });
      toast('Course added to path');
      await openPathDetail(data.path_id);
    } else if (kind === 'award') {
      const data = formData(form);
      await api('/api/v1/badges/' + data.badge_id + '/award', {
        method: 'POST', body: JSON.stringify({ employee_id: data.employee_id }),
      });
      toast('Badge awarded');
      await loadCredentials();
    }
  } catch (error) {
    toast(error.message, true);
  }
});

$('#catalogFilter').addEventListener('submit', (event) => {
  event.preventDefault();
  loadCatalog(formData(event.target));
});

$('#peopleFilter').addEventListener('submit', (event) => {
  event.preventDefault();
  loadPeople(formData(event.target));
});

$('#auditFilter').addEventListener('submit', (event) => {
  event.preventDefault();
  loadAudit(formData(event.target));
});

$('#learnerSelect').addEventListener('change', () => loadLearning());
$('#gradebookLearner').addEventListener('change', () => loadGradebook());
$('#discussionCourse').addEventListener('change', () => loadDiscussions());

$('#verifyForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const result = $('#verifyResult');
  try {
    const data = await api('/api/v1/credentials/verify/' + encodeURIComponent(new FormData(event.target).get('code')));
    result.hidden = false;
    result.innerHTML = `<strong>Verified credential:</strong> ${esc(data.badge_title)} — awarded to ${esc(data.display_name)} on ${esc((data.awarded_at || '').slice(0, 10))}` +
      (data.course_title ? `<br>Course: ${esc(data.course_title)}` : '') +
      (data.path_title ? `<br>Path: ${esc(data.path_title)}` : '');
  } catch (error) {
    result.hidden = false;
    result.textContent = error.message;
  }
});

$$('nav button').forEach((button) => {
  button.addEventListener('click', () => showView(button.dataset.view));
});

$('#role').addEventListener('change', () => {
  const actor = $('#actor').value.trim();
  if (!actor || Object.values(ROLE_DEFAULTS).includes(actor)) {
    $('#actor').value = ROLE_DEFAULTS[$('#role').value] || 'demo-admin';
  }
  refreshSession().catch((error) => toast(error.message, true));
});

$('#actor').addEventListener('change', () => {
  refreshSession().catch((error) => toast(error.message, true));
});

/* ------------------------------------------------------------------- boot */

(async () => {
  try {
    const health = await api('/api/v1/health');
    const meta = await api('/api/v1/meta');
    $('#version').textContent = 'v' + health.version;
    const attributeSelect = $('#policyDialog form [name=attribute]');
    if (attributeSelect && meta.policy_attributes) {
      attributeSelect.innerHTML = meta.policy_attributes.map((name) =>
        `<option>${esc(name)}</option>`).join('');
    }
    $('#role').value = 'platform_admin';
    $('#actor').value = ROLE_DEFAULTS.platform_admin;
    if (!location.hash) history.replaceState(null, '', '#/dashboard');
    lastRouteHash = location.hash;
    await refreshSession({ reload: false });
    await Promise.all([loadDashboard(), loadCatalog()]);
    if (location.hash !== '#/dashboard') routeFromHash(true);
  } catch (error) {
    toast(error.message, true);
  }
})();
