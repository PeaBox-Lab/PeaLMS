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
  auditOffset: 0,
};

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

function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.remove('show'), 4000);
}

function authHeaders() {
  return state.token ? { Authorization: 'Bearer ' + state.token } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(options.headers || {}) },
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
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || 'Unable to create demo session');
  state.token = data.token;
}

async function refreshSession({ reload = true } = {}) {
  const role = $('#role').value;
  const actor = $('#actor').value.trim() || ROLE_DEFAULTS[role] || 'demo-admin';
  $('#actor').value = actor;
  await createSession(role, actor);
  state.me = await api('/api/v1/me');
  state.permissions = new Set(state.me.permissions || []);
  state.employees = [];
  applyPermissions();
  updateBell(state.me.unread_notifications || 0);
  if (reload) await loadView(state.view);
}

function applyPermissions() {
  $$('[data-perm]').forEach((el) => {
    const allowed = can(el.dataset.perm);
    el.hidden = !allowed;
    el.classList.toggle('is-hidden', !allowed);
  });
  const activeNav = $('nav button.active');
  if (activeNav && activeNav.hidden) showView('dashboard');
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
  return `<span class="tag ${map[value] || ''}">${esc(value)}</span>`;
}

function table(headers, rows) {
  if (!rows.length) return '<div class="empty">Nothing to show.</div>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>` +
    `<tbody>${rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

/* -------------------------------------------------------------- dashboard */

async function loadDashboard() {
  try {
    const data = await api('/api/v1/dashboard');
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
    toast(error.message, true);
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
  try {
    if (query) state.catalogQuery = query;
    const params = new URLSearchParams();
    Object.entries(state.catalogQuery).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const suffix = params.toString() ? '?' + params.toString() : '';
    state.courses = await api('/api/v1/courses' + suffix);
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
    toast(error.message, true);
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
  try {
    const course = await api('/api/v1/courses/' + encodeURIComponent(code));
    state.course = course;
    state.lessonId = lessonId || defaultLessonId(course);
    activateView('course');
    renderCourse();
    await renderPlayer();
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshCourse(render = true) {
  if (!state.course) return null;
  const course = await api('/api/v1/courses/' + encodeURIComponent(state.course.course_code));
  state.course = course;
  if (render) {
    renderCourse();
    await renderPlayer();
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

async function renderPlayer() {
  const mount = $('#playerMount');
  if (!mount) return;
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
      : '<div class="empty">No video attached to this lesson.</div>';
  } else if (lesson.type === 'link') {
    body = `<p>This lesson opens an external resource.</p>
      <p><a class="button-link" href="${esc(lesson.content)}" target="_blank" rel="noopener noreferrer">Open resource ↗</a></p>`;
  } else if (lesson.type === 'quiz') {
    await renderQuiz(lesson, module, done);
    return;
  }
  mount.innerHTML = playerShell(module, lesson, body, done);
  if (lesson.type === 'video') attachVideoProgress(lesson);
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
}

async function renderQuiz(lesson, module, done) {
  const mount = $('#playerMount');
  try {
    const quiz = await api(`/api/v1/lessons/${lesson.id}/quiz`);
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
  const response = await fetch('/api/v1/media', {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': file.type, 'X-Filename': file.name },
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
  if (can('manage_people')) {
    if (!state.employees.length) {
      const page = await api('/api/v1/employees?limit=500');
      state.employees = page.items;
    }
    return state.employees
      .filter((employee) => employee.employment_status === 'active')
      .map((employee) => ({ id: employee.employee_id, name: employee.display_name }));
  }
  if (can('read_team')) {
    const team = await api('/api/v1/team');
    const options = (team.members || []).map((member) => ({ id: member.employee_id, name: member.display_name }));
    const selfId = state.me.employee_id;
    if (selfId && !options.some((option) => option.id === selfId)) {
      options.unshift({ id: selfId, name: (state.me.display_name || selfId) + ' (me)' });
    }
    return options;
  }
  return [{ id: state.me.employee_id, name: state.me.display_name || state.me.employee_id }];
}

async function loadLearning() {
  try {
    const select = $('#learnerSelect');
    const options = await learnerOptions();
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
    const items = page.items || [];
    if (!items.length) {
      $('#learningList').innerHTML = '<div class="empty">No assigned learning.</div>';
      return;
    }
    const self = state.me.employee_id === learnerId;
    const canComplete = self || can('complete_any') || can('complete_team');
    const canManage = can('manage_learning') || can('read_team');
    $('#learningList').innerHTML = items.map((item) => {
      const open = ['assigned', 'in_progress', 'failed'].includes(item.status);
      const actions = [];
      if (open) {
        actions.push(`<button type="button" data-action="open-course" data-course="${esc(item.course_code)}">Open course</button>`);
      } else if (item.status === 'completed') {
        actions.push(`<button type="button" class="secondary" data-action="open-course" data-course="${esc(item.course_code)}">Review course</button>`);
      }
      if (open && canComplete && item.status === 'assigned') {
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
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------- team */

async function loadTeam() {
  try {
    const [team, assignments] = await Promise.all([
      api('/api/v1/team'),
      api('/api/v1/team/assignments?limit=200'),
    ]);
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
    toast(error.message, true);
  }
}

/* ----------------------------------------------------------------- people */

async function loadPeople(query = {}) {
  try {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value) params.set(key, value); });
    params.set('limit', '200');
    const page = await api('/api/v1/employees?' + params.toString());
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
    toast(error.message, true);
  }
}

/* --------------------------------------------------------------- policies */

async function loadPolicies() {
  try {
    const policies = await api('/api/v1/policies');
    if (!state.courses.length) await loadCatalog();
    $('#policyRows').innerHTML = policies.map((policy) => `<tr>` +
      `<td>${esc(policy.name)}</td>` +
      `<td>${esc(policy.course_code)}<br><small>${esc(policy.course_title)}</small></td>` +
      `<td><code>${esc(policy.attribute)} ${esc(policy.operator)} ${esc(policy.value)}</code></td>` +
      `<td>${esc(policy.due_days)} days</td>` +
      `<td>${statusTag(policy.active ? 'active' : 'inactive')}</td>` +
      `</tr>`).join('');
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------------------------------------------------------- notifications */

async function loadNotifications() {
  try {
    const page = await api('/api/v1/notifications?limit=100');
    const items = page.items || [];
    $('#notificationList').innerHTML = items.length ? items.map((item) => `<article class="card ${item.read_at ? 'muted-card' : ''}">` +
      `<div class="meta"><span class="tag">${esc(item.template)}</span>` +
      (item.read_at ? '<span class="tag green">Read</span>' : '<span class="tag amber">Unread</span>') +
      `<span class="tag">${esc((item.created_at || '').slice(0, 16).replace('T', ' '))}</span></div>` +
      `<h3>${esc(item.subject)}</h3><p class="prewrap">${esc(item.body)}</p>` +
      (item.read_at ? '' : `<div class="actions"><button type="button" class="secondary" data-action="mark-read" data-id="${esc(item.id)}">Mark read</button></div>`) +
      `</article>`).join('') : '<div class="empty">No notifications.</div>';
    const unread = items.filter((item) => !item.read_at).length;
    updateBell(unread);
  } catch (error) {
    toast(error.message, true);
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

async function readCsv() {
  const file = $('#csvFile').files[0];
  if (!file) throw new Error('Select a CSV file');
  state.csvText = await file.text();
  state.csvName = file.name;
}

async function analyzeImport() {
  try {
    await readCsv();
    const result = await api('/api/v1/hris/analyze', { method: 'POST', body: JSON.stringify(importBody()) });
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
    toast('Dry run complete');
  } catch (error) {
    toast(error.message, true);
  }
}

async function applyImport() {
  if (!window.confirm('Apply this employee extract? Account, role, and assignment changes will be audited.')) return;
  try {
    const result = await api('/api/v1/hris/apply', { method: 'POST', body: JSON.stringify(importBody()) });
    toast(result.status === 'duplicate' ? 'This extract was already applied' : 'Import applied');
    $('#applyImport').disabled = true;
    await Promise.all([loadImportJobs(), loadDashboard()]);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadImportJobs() {
  try {
    const jobs = await api('/api/v1/import-jobs');
    $('#importRows').innerHTML = jobs.map((job) => `<tr>` +
      `<td>#${esc(job.id)}</td><td>${esc(job.filename)}</td><td>${statusTag(job.status)}</td>` +
      `<td>${esc(job.row_count)}</td><td>${esc(job.create_count)}</td>` +
      `<td>${esc(job.update_count)}</td><td>${esc(job.suspend_count)}</td></tr>`).join('');
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ audit */

async function loadAudit(query = {}) {
  try {
    const params = new URLSearchParams({ limit: '100' });
    Object.entries(query).forEach(([key, value]) => { if (value) params.set(key, value); });
    const page = await api('/api/v1/audit?' + params.toString());
    $('#auditRows').innerHTML = page.items.map((event) => `<tr>` +
      `<td>${esc((event.created_at || '').slice(0, 19).replace('T', ' '))}</td>` +
      `<td>${esc(event.event_type)}</td><td>${esc(event.actor)}</td>` +
      `<td>${esc(event.entity_type)}<br><small>${esc(event.entity_id)}</small></td>` +
      `<td><code>${esc(JSON.stringify(event.details))}</code></td></tr>`).join('');
    $('#auditMeta').textContent = `${page.total} event(s) · showing ${page.items.length}`;
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ views */

const VIEW_LOADERS = {
  dashboard: loadDashboard,
  catalog: () => loadCatalog(),
  course: () => (state.course ? openCourse(state.course.course_code, state.lessonId) : showView('catalog')),
  learning: loadLearning,
  team: loadTeam,
  people: () => loadPeople(),
  policies: loadPolicies,
  notifications: loadNotifications,
  hris: loadImportJobs,
  audit: () => loadAudit(),
};

function activateView(name) {
  state.view = name;
  $$('nav button').forEach((item) => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach((section) => section.classList.toggle('active', section.id === name));
}

function showView(name) {
  const button = $(`nav button[data-view="${name}"]`);
  if (button && button.hidden) name = 'dashboard';
  activateView(name);
  return loadView(name);
}

async function loadView(name) {
  const loader = VIEW_LOADERS[name];
  if (loader) await loader();
}

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
    const response = await fetch('/api/v1/reports/compliance.csv', { headers: authHeaders() });
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
      openDialog(target.dataset.dialog);
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
        course_code: data.course_code, title: data.title, category: data.category,
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
        quiz_pass_score: Number(data.quiz_pass_score || 70),
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
    $('#version').textContent = 'v' + health.version;
    $('#role').value = 'platform_admin';
    $('#actor').value = ROLE_DEFAULTS.platform_admin;
    await refreshSession({ reload: false });
    await Promise.all([loadDashboard(), loadCatalog()]);
  } catch (error) {
    toast(error.message, true);
  }
})();
