/* מכונית תלת-ממד בקווי מתאר בלבד - ללא ספריות חיצוניות.
 *
 * המודל נבנה בקוד ולא נטען מקובץ: כמה עשרות קילובייט של GLTF ומנוע
 * WebGL שלם היו יורדים לכל מבקר רק בשביל מסך הפתיחה. כאן זה חישוב
 * מטריצות קטן על canvas דו-ממדי, שגם עובד אופליין כמו שאר ה-PWA.
 *
 * הצללה אין - הרכב שקוף, וכל מה שנראה הוא הקווים. עומק נמסר דרך
 * שקיפות: קווים רחוקים דהויים ודקים, קרובים בהירים וזוהרים.
 */
(function (global) {
  "use strict";

  var TAU = Math.PI * 2;

  // ---------- בניית הגאומטריה ----------

  function Mesh() {
    this.points = [];   // [x, y, z, x, y, z, ...]
    this.edges = [];    // [a, b, a, b, ...] אינדקסים לזוגות נקודות
  }

  Mesh.prototype.vertex = function (x, y, z) {
    this.points.push(x, y, z);
    return this.points.length / 3 - 1;
  };

  Mesh.prototype.edge = function (a, b) {
    this.edges.push(a, b);
  };

  /** משרשר טבעת נקודות בקו סגור או פתוח. */
  Mesh.prototype.ring = function (indices, closed) {
    for (var i = 0; i < indices.length - 1; i++) {
      this.edge(indices[i], indices[i + 1]);
    }
    if (closed && indices.length > 2) {
      this.edge(indices[indices.length - 1], indices[0]);
    }
  };

  /** קווי אורך בין שני חתכים - זה מה שהופך ערימת טבעות לגוף אחד. */
  Mesh.prototype.loft = function (a, b) {
    for (var i = 0; i < a.length && i < b.length; i++) {
      this.edge(a[i], b[i]);
    }
  };

  /** מלבן מעוגל במישור Y-Z: חתך הרוחב של המרכב. */
  function bodySection(mesh, x, halfWidth, bottom, top, radius, perCorner) {
    var r = Math.min(radius, halfWidth, (top - bottom) / 2);
    var corners = [
      [halfWidth - r, top - r],
      [-halfWidth + r, top - r],
      [-halfWidth + r, bottom + r],
      [halfWidth - r, bottom + r],
    ];
    var indices = [];
    for (var c = 0; c < 4; c++) {
      var start = (c * TAU) / 4;
      for (var i = 0; i < perCorner; i++) {
        var angle = start + (TAU / 4) * (i / perCorner);
        indices.push(mesh.vertex(
          x,
          corners[c][1] + r * Math.sin(angle),
          corners[c][0] + r * Math.cos(angle)
        ));
      }
    }
    return indices;
  }

  /** קשת התא: מקו החגורה מצד אחד, מעל הגג, ובחזרה לצד השני. */
  function cabinArch(mesh, x, halfWidth, belt, height, steps) {
    var indices = [];
    for (var i = 0; i < steps; i++) {
      var angle = Math.PI * (1 - i / (steps - 1));
      var c = Math.cos(angle);
      var s = Math.max(0, Math.sin(angle));
      // מעריך קטן מ-1 משטח את הגג ומשאיר פינות מעוגלות, כמו גג אמיתי
      indices.push(mesh.vertex(
        x,
        belt + height * Math.pow(s, 0.62),
        halfWidth * (c < 0 ? -1 : 1) * Math.pow(Math.abs(c), 0.62)
      ));
    }
    return indices;
  }

  /** גלגל: שתי טבעות במרחק רוחב הצמיג, חישוק ו-חישורים. */
  function wheel(mesh, cx, cy, cz, radius, width, steps) {
    var outer = [[], []];
    var side, i, angle;
    for (side = 0; side < 2; side++) {
      var z = cz + (side === 0 ? -width : width);
      for (i = 0; i < steps; i++) {
        angle = (i / steps) * TAU;
        outer[side].push(mesh.vertex(
          cx + radius * Math.cos(angle),
          cy + radius * Math.sin(angle),
          z
        ));
      }
      mesh.ring(outer[side], true);
    }
    // דופן הצמיג - כמה שלבים מספיקים לנפח; יותר מזה סותם את הגלגל
    for (i = 0; i < steps; i += 4) {
      mesh.edge(outer[0][i], outer[1][i]);
    }

    // חישוק וחישורים על הפן החיצוני של הגלגל
    var faceZ = cz + (cz > 0 ? width : -width);
    var hub = [];
    var hubSteps = Math.max(6, Math.round(steps / 2));
    for (i = 0; i < hubSteps; i++) {
      angle = (i / hubSteps) * TAU;
      hub.push(mesh.vertex(
        cx + radius * 0.42 * Math.cos(angle),
        cy + radius * 0.42 * Math.sin(angle),
        faceZ
      ));
    }
    mesh.ring(hub, true);
    var outerFace = cz > 0 ? outer[1] : outer[0];
    for (i = 0; i < hubSteps; i += 2) {
      mesh.edge(hub[i], outerFace[Math.round((i / hubSteps) * steps) % steps]);
    }
  }

  // חתכי המרכב מהחלק האחורי לחזית. הפרופורציות הן של סדאן אמיתי:
  // אורך 4.4 יחידות מול גובה 1.4 - הקפדה על היחס הזה היא ההבדל בין
  // רכב לבין בועה על גלגלים. bottom עולה מעל הגלגלים, ושם נוצרות
  // קשתות הגלגלים שנותנות לצללית את הצורה שלה.
  var BODY = [
    { x: -2.20, w: 0.60, bottom: -0.50, top: -0.24, r: 0.10 },
    { x: -2.06, w: 0.78, bottom: -0.60, top: -0.10, r: 0.13 },
    { x: -1.86, w: 0.86, bottom: -0.64, top: -0.04, r: 0.15 },
    { x: -1.66, w: 0.88, bottom: -0.62, top: -0.03, r: 0.15 },
    { x: -1.53, w: 0.88, bottom: -0.46, top: -0.02, r: 0.15 },
    { x: -1.35, w: 0.88, bottom: -0.28, top: -0.02, r: 0.13 },
    { x: -1.17, w: 0.88, bottom: -0.46, top: -0.02, r: 0.15 },
    { x: -1.02, w: 0.88, bottom: -0.66, top: -0.02, r: 0.15 },
    { x: -0.60, w: 0.88, bottom: -0.70, top: -0.02, r: 0.15 },
    { x: 0.00, w: 0.88, bottom: -0.70, top: -0.02, r: 0.15 },
    { x: 0.62, w: 0.88, bottom: -0.70, top: -0.02, r: 0.15 },
    { x: 1.02, w: 0.88, bottom: -0.66, top: -0.04, r: 0.15 },
    { x: 1.17, w: 0.88, bottom: -0.46, top: -0.05, r: 0.15 },
    { x: 1.35, w: 0.87, bottom: -0.28, top: -0.06, r: 0.13 },
    { x: 1.53, w: 0.87, bottom: -0.46, top: -0.07, r: 0.15 },
    { x: 1.68, w: 0.86, bottom: -0.62, top: -0.08, r: 0.15 },
    { x: 1.92, w: 0.82, bottom: -0.62, top: -0.12, r: 0.14 },
    { x: 2.12, w: 0.74, bottom: -0.58, top: -0.19, r: 0.12 },
    { x: 2.24, w: 0.58, bottom: -0.50, top: -0.28, r: 0.10 },
  ];

  // התא: שמשה קדמית משופעת, גג, וחלון אחורי יורד לתא המטען
  var CABIN = [
    { x: 0.80, w: 0.84, belt: -0.05, h: 0.03 },
    { x: 0.52, w: 0.83, belt: -0.04, h: 0.23 },
    { x: 0.20, w: 0.81, belt: -0.03, h: 0.39 },
    { x: -0.24, w: 0.80, belt: -0.02, h: 0.44 },
    { x: -0.72, w: 0.79, belt: -0.02, h: 0.43 },
    { x: -1.08, w: 0.78, belt: -0.02, h: 0.32 },
    { x: -1.40, w: 0.76, belt: -0.03, h: 0.11 },
    { x: -1.58, w: 0.71, belt: -0.04, h: 0.01 },
  ];

  var WHEELS = [
    { x: 1.35, z: 0.78 }, { x: 1.35, z: -0.78 },
    { x: -1.35, z: 0.78 }, { x: -1.35, z: -0.78 },
  ];

  var WHEEL_RADIUS = 0.33;
  var WHEEL_Y = -0.67;
  var GROUND_Y = WHEEL_Y - WHEEL_RADIUS;

  function buildCar(detail) {
    var mesh = new Mesh();
    var perCorner = detail ? 5 : 4;
    var previous = null;
    var i;

    for (i = 0; i < BODY.length; i++) {
      var s = BODY[i];
      var section = bodySection(mesh, s.x, s.w, s.bottom, s.top, s.r, perCorner);
      mesh.ring(section, true);
      if (previous) {
        mesh.loft(previous, section);
      }
      previous = section;
    }

    previous = null;
    var archSteps = detail ? 13 : 9;
    for (i = 0; i < CABIN.length; i++) {
      var c = CABIN[i];
      var arch = cabinArch(mesh, c.x, c.w, c.belt, c.h, archSteps);
      mesh.ring(arch, false);
      if (previous) {
        mesh.loft(previous, arch);
      }
      previous = arch;
    }

    var wheelSteps = detail ? 20 : 14;
    for (i = 0; i < WHEELS.length; i++) {
      wheel(mesh, WHEELS[i].x, WHEEL_Y, WHEELS[i].z,
            WHEEL_RADIUS, 0.10, wheelSteps);
    }

    return {
      points: new Float32Array(mesh.points),
      edges: new Uint16Array(mesh.edges),
    };
  }

  // ---------- ציור ----------

  var DEFAULTS = {
    near: "#e8faff",
    mid: "#5cd2f5",
    far: "#2b7fb0",
    glow: "#38bdf8",
    spin: 0.34,        // רדיאנים לשנייה
    tilt: -0.20,       // הטיית מצלמה, שלילי = מסתכלים מעט מלמעלה
    // מצלמה רחוקה עם עדשה ארוכה: פרספקטיבה מתונה, כמו בשרטוט טכני.
    // מקרוב החזית הייתה מתנפחת והרכב היה נראה כמו צעצוע רחב עיניים.
    distance: 15,
    reflection: 0.16,
  };

  function Car3D(canvas, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.options = Object.assign({}, DEFAULTS, options || {});
    this.model = buildCar(!isCoarsePointer());
    this.projected = new Float32Array((this.model.points.length / 3) * 3);
    this.yaw = -0.7;
    this.pointer = { x: 0, y: 0 };
    this.hover = 0;
    this.warp = 0;
    this.running = false;
    this.reduced = prefersReducedMotion();
    this.bounds = { minX: 0, minY: 0, maxX: 0, maxY: 0 };
    this.resize();
  }

  function prefersReducedMotion() {
    return !!(global.matchMedia &&
      global.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function isCoarsePointer() {
    return !!(global.matchMedia &&
      global.matchMedia("(pointer: coarse)").matches);
  }

  Car3D.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    this.width = Math.max(1, Math.round(rect.width));
    this.height = Math.max(1, Math.round(rect.height));
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // הרכב מסתובב, ולכן הקנה מידה נגזר מהמצב הכי "רחב" שלו לאורך
    // הסיבוב כולו. חישוב לפי האורך בלבד היה מגלש את החזית מהמסך
    // ברגע שהמכונית עומדת באלכסון.
    var extent = this.extent();
    // במסך צר הרכב היה נוגע בשוליים משני הצדדים - שם נותנים לו פחות
    var fill = this.width < 700 ? 0.42 : 0.46;
    this.focal = Math.min(
      (fill * this.width) / extent.x,
      (0.44 * this.height) / extent.y
    );
    this.centerX = this.width / 2;
    // מעט מעל המרכז - ההשתקפות תופסת את המקום שמתחת
    this.centerY = this.height * 0.46;
  };

  /** החצי-מימד הגדול ביותר (ליחידת focal) על פני כל זוויות הסיבוב. */
  Car3D.prototype.extent = function () {
    if (this._extent) return this._extent;
    var points = this.model.points;
    var distance = this.options.distance;
    var maxX = 0, maxY = 0;
    var tilts = [this.options.tilt - 0.2, this.options.tilt, this.options.tilt + 0.2];

    for (var t = 0; t < tilts.length; t++) {
      var cosT = Math.cos(tilts[t]), sinT = Math.sin(tilts[t]);
      for (var a = 0; a < 24; a++) {
        var yaw = (a / 24) * TAU;
        var cosY = Math.cos(yaw), sinY = Math.sin(yaw);
        for (var i = 0; i < points.length; i += 3) {
          var x = points[i], y = points[i + 1], z = points[i + 2];
          var rx = x * cosY + z * sinY;
          var rz = z * cosY - x * sinY;
          var ry = y * cosT - rz * sinT;
          var depth = y * sinT + rz * cosT + distance;
          maxX = Math.max(maxX, Math.abs(rx) / depth);
          maxY = Math.max(maxY, Math.abs(ry) / depth);
        }
      }
    }
    this._extent = { x: maxX, y: maxY };
    return this._extent;
  };

  /** מסובב, מטיל למסך ומחזיר את טווח העומק לצורך דירוג השקיפות. */
  Car3D.prototype.project = function (yaw, tilt, dolly) {
    var points = this.model.points;
    var out = this.projected;
    var cosY = Math.cos(yaw), sinY = Math.sin(yaw);
    var cosT = Math.cos(tilt), sinT = Math.sin(tilt);
    var distance = this.options.distance - dolly;
    var focal = this.focal;
    var cx = this.centerX, cy = this.centerY;
    var minDepth = Infinity, maxDepth = -Infinity;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    for (var i = 0, j = 0; i < points.length; i += 3, j += 3) {
      var x = points[i], y = points[i + 1], z = points[i + 2];
      var rx = x * cosY + z * sinY;
      var rz = z * cosY - x * sinY;
      var ry = y * cosT - rz * sinT;
      var depth = y * sinT + rz * cosT + distance;
      if (depth < 0.1) depth = 0.1;
      var scale = focal / depth;
      var sx = cx + rx * scale;
      var sy = cy - ry * scale;
      out[j] = sx;
      out[j + 1] = sy;
      out[j + 2] = depth;
      if (depth < minDepth) minDepth = depth;
      if (depth > maxDepth) maxDepth = depth;
      if (sx < minX) minX = sx;
      if (sx > maxX) maxX = sx;
      if (sy < minY) minY = sy;
      if (sy > maxY) maxY = sy;
    }

    this.bounds = { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
    this.depthRange = maxDepth - minDepth || 1;
    this.minDepth = minDepth;
    // קו המגע של הגלגלים - עליו משתקפת המכונית
    this.groundY = maxY;
  };

  /** צובר את הקטעים לשלוש שכבות עומק, כדי לצייר הכל בשלוש משיכות. */
  Car3D.prototype.strokeLayers = function (alphaScale, mirror) {
    var ctx = this.ctx;
    var edges = this.model.edges;
    var p = this.projected;
    var layers = [[], [], []];
    var i;

    for (i = 0; i < edges.length; i += 2) {
      var a = edges[i] * 3, b = edges[i + 1] * 3;
      var t = ((p[a + 2] + p[b + 2]) / 2 - this.minDepth) / this.depthRange;
      var layer = t < 0.34 ? 0 : (t < 0.62 ? 1 : 2);
      layers[layer].push(p[a], p[a + 1], p[b], p[b + 1]);
    }

    var styles = [
      { color: this.options.near, alpha: 0.95, width: 1.45, glow: 9 },
      { color: this.options.mid, alpha: 0.52, width: 1.05, glow: 0 },
      { color: this.options.far, alpha: 0.26, width: 0.85, glow: 0 },
    ];

    for (var l = layers.length - 1; l >= 0; l--) {
      var segments = layers[l];
      if (!segments.length) continue;
      var style = styles[l];
      ctx.beginPath();
      for (i = 0; i < segments.length; i += 4) {
        ctx.moveTo(segments[i], segments[i + 1]);
        ctx.lineTo(segments[i + 2], segments[i + 3]);
      }
      ctx.globalAlpha = style.alpha * alphaScale;
      ctx.lineWidth = style.width;
      // ההשתקפות דוהה ככל שמתרחקים מקו המגע, אחרת הגג נחתך באוויר
      ctx.strokeStyle = mirror
        ? this.mirrorGradient(style.color)
        : style.color;
      if (style.glow && !mirror) {
        ctx.shadowColor = this.options.glow;
        ctx.shadowBlur = style.glow + this.hover * 10;
      } else {
        ctx.shadowBlur = 0;
      }
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
  };

  /** אותו צבע בשקיפות נתונה. הדהייה חייבת להיות של הצבע עצמו,
   *  אחרת המעבר עובר דרך לבן והשתקפות מקבלת הילה מלוכלכת. */
  function fade(hex, alpha) {
    var value = parseInt(hex.slice(1), 16);
    return "rgba(" + ((value >> 16) & 255) + ", " + ((value >> 8) & 255) +
      ", " + (value & 255) + ", " + alpha + ")";
  }

  /** מעבר צבע לשקוף, לשימוש כקו ההשתקפות. */
  Car3D.prototype.mirrorGradient = function (color) {
    var gradient = this.ctx.createLinearGradient(
      0, this.groundY, 0, this.bounds.minY
    );
    gradient.addColorStop(0, fade(color, 1));
    gradient.addColorStop(1, fade(color, 0));
    return gradient;
  };

  Car3D.prototype.drawFloor = function () {
    var ctx = this.ctx;
    var radiusX = (this.bounds.maxX - this.bounds.minX) * 0.62;
    var radiusY = radiusX * 0.16;
    var y = this.groundY;
    var gradient = ctx.createRadialGradient(
      this.centerX, y, 0, this.centerX, y, Math.max(radiusX, 1)
    );
    gradient.addColorStop(0, "rgba(56, 189, 248, " + (0.20 + this.hover * 0.12) + ")");
    gradient.addColorStop(1, "rgba(56, 189, 248, 0)");
    ctx.save();
    ctx.translate(this.centerX, y);
    ctx.scale(1, radiusY / Math.max(radiusX, 1));
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(radiusX, 1), 0, TAU);
    ctx.restore();
    ctx.fillStyle = gradient;
    ctx.fill();
  };

  Car3D.prototype.render = function () {
    var ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    this.drawFloor();

    // ההשתקפות: אותם קטעים, משוקפים סביב קו המגע ודהויים
    if (this.options.reflection > 0) {
      ctx.save();
      ctx.translate(0, this.groundY);
      ctx.scale(1, -0.55);
      ctx.translate(0, -this.groundY);
      this.strokeLayers(this.options.reflection, true);
      ctx.restore();
    }

    this.strokeLayers(1, false);
  };

  Car3D.prototype.frame = function (now) {
    if (!this.running) return;
    var elapsed = this.last ? Math.min((now - this.last) / 1000, 0.05) : 0;
    this.last = now;

    if (this.warp > 0) {
      this.warp = Math.min(1, this.warp + elapsed * 2.4);
    }

    var spin = this.reduced ? 0 : this.options.spin * (1 + this.hover * 0.5);
    this.yaw += (spin + this.warp * 5) * elapsed;

    // המצביע מוסיף נדנוד קל - מספיק כדי שהמכונית תרגיש נוכחת בחדר
    var tilt = this.options.tilt + this.pointer.y * 0.16;
    var sway = this.reduced ? 0 : this.pointer.x * 0.22;
    var bob = this.reduced ? 0 : Math.sin(now / 1400) * 0.02;
    // הצניחה פנימה נמדדת ביחס למרחק המצלמה, אחרת "הכניסה" למכונית
    // הייתה זזה בכמה אחוזים בלבד ולא מרגישה כמו מעבר
    var dolly = this.warp * this.warp * this.options.distance * 0.78;

    this.project(this.yaw + sway, tilt + bob, dolly);
    this.render();

    if (this.warp >= 1 && this.onWarpEnd) {
      var done = this.onWarpEnd;
      this.onWarpEnd = null;
      done();
      return;
    }
    global.requestAnimationFrame(this.frame.bind(this));
  };

  Car3D.prototype.start = function () {
    if (this.running) return;
    this.running = true;
    this.last = 0;
    global.requestAnimationFrame(this.frame.bind(this));
  };

  Car3D.prototype.stop = function () {
    this.running = false;
  };

  Car3D.prototype.setHover = function (on) {
    this.hover = on ? 1 : 0;
  };

  Car3D.prototype.setPointer = function (x, y) {
    this.pointer.x = x;
    this.pointer.y = y;
  };

  /** אנימציית הכניסה: המכונית מאיצה וטסה לעבר הצופה. */
  Car3D.prototype.enter = function (done) {
    if (this.reduced || this.warp > 0) {
      done();
      return;
    }
    this.warp = 0.001;
    this.onWarpEnd = done;
  };

  function mount(canvas, options) {
    var car = new Car3D(canvas, options);
    var resizeTimer = null;

    global.addEventListener("resize", function () {
      global.clearTimeout(resizeTimer);
      resizeTimer = global.setTimeout(function () {
        car.resize();
      }, 120);
    });

    // אין טעם לצייר טאב שלא רואים - זו סוללה של מישהו
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        car.stop();
      } else {
        car.start();
      }
    });

    car.start();
    return car;
  }

  global.Car3D = { mount: mount, build: buildCar };
})(window);
