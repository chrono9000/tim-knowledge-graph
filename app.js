(function () {
  "use strict";
  const data = window.KNOWLEDGE_GRAPH;
  const svg = document.querySelector("#graph-svg");
  const graphPanel = document.querySelector(".graph-panel");
  const viewport = document.querySelector("#viewport");
  const linksLayer = document.querySelector("#links");
  const nodesLayer = document.querySelector("#nodes");
  const search = document.querySelector("#search");
  const filterList = document.querySelector("#category-filters");
  const visibleCount = document.querySelector("#visible-count");
  const emptyState = document.querySelector("#empty-state");
  const NS = "http://www.w3.org/2000/svg";
  const categories = new Map(data.categories.map(c => [c.id, c]));
  const nodeById = new Map();
  const activeCategories = new Set(categories.keys());
  let selectedId = null;
  let transform = { x: 0, y: 0, k: 1 };
  let pan = null;
  let pinch = null;
  const touchPoints = new Map();
  let dragging = null;
  let animationId;
  let alpha = 1;

  const nodes = data.nodes.map((node, index) => {
    const angle = index * 2.39996;
    const radius = 34 + Math.sqrt(index) * 24;
    const labelWidth = Math.max(82, Math.min(156, node.label.length * 6.7 + 22));
    const full = { ...node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0, width: labelWidth + 18, height: 38 };
    nodeById.set(node.id, full);
    return full;
  });
  const links = data.links.map(link => ({ ...link, a: nodeById.get(link.source), b: nodeById.get(link.target) }));

  function makeSvg(tag, attrs = {}) {
    const element = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  data.categories.forEach(category => {
    const count = nodes.filter(n => n.category === category.id).length;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter";
    button.dataset.category = category.id;
    button.setAttribute("aria-pressed", "true");
    button.style.setProperty("--dot", category.color);
    button.innerHTML = `<span class="dot" aria-hidden="true"></span><span>${category.label}</span><span class="count">${count}</span>`;
    button.addEventListener("click", () => {
      activeCategories.has(category.id) ? activeCategories.delete(category.id) : activeCategories.add(category.id);
      button.setAttribute("aria-pressed", String(activeCategories.has(category.id)));
      applyFilters();
    });
    filterList.appendChild(button);
  });

  links.forEach(link => {
    link.el = makeSvg("line", { class: "link" });
    linksLayer.appendChild(link.el);
  });

  nodes.forEach(node => {
    const category = categories.get(node.category);
    const g = makeSvg("g", { class: "node", tabindex: "0", role: "button", "aria-label": `${node.label}, ${category.label}` });
    g.style.setProperty("--node-color", category.color);
    const left = -node.width / 2;
    const halo = makeSvg("circle", { class: "halo", cx: left + 10, r: 17, stroke: category.color });
    const core = makeSvg("circle", { class: "core", cx: left + 10, r: 7, fill: category.color, stroke: "#101521" });
    const bg = makeSvg("rect", { class: "label-bg", x: left, y: -13, width: node.width, height: 26, rx: 7 });
    const tick = makeSvg("line", { class: "category-tick", x1: left + 21, x2: left + 21, y1: -5, y2: 5, stroke: category.color });
    const text = makeSvg("text", { x: left + 29, y: 4 }); text.textContent = node.label;
    g.append(halo, core, bg, tick, text);
    g.addEventListener("pointerdown", event => startNodeDrag(event, node));
    g.addEventListener("click", event => { event.stopPropagation(); if (!node.wasDragged) selectNode(node.id); });
    g.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(node.id); } });
    node.el = g;
    nodesLayer.appendChild(g);
  });

  function visibleNode(node) {
    const query = search.value.trim().toLowerCase();
    return activeCategories.has(node.category) && (!query || node.label.toLowerCase().includes(query) || node.description.toLowerCase().includes(query));
  }

  function applyFilters() {
    const visible = new Set(nodes.filter(visibleNode).map(n => n.id));
    nodes.forEach(n => n.el.classList.toggle("filtered", !visible.has(n.id)));
    links.forEach(l => l.el.classList.toggle("filtered", !visible.has(l.source) || !visible.has(l.target)));
    visibleCount.textContent = visible.size;
    emptyState.hidden = visible.size !== 0;
    if (selectedId && !visible.has(selectedId)) clearSelection();
    alpha = .7;
    startSimulation();
  }

  function selectNode(id) {
    selectedId = id;
    const node = nodeById.get(id);
    const category = categories.get(node.category);
    const neighborIds = new Set();
    links.forEach(link => {
      const connected = link.source === id || link.target === id;
      link.el.classList.toggle("highlighted", connected);
      link.el.classList.toggle("dimmed", !connected);
      if (connected) neighborIds.add(link.source === id ? link.target : link.source);
    });
    nodes.forEach(n => {
      n.el.classList.toggle("selected", n.id === id);
      n.el.classList.toggle("dimmed", n.id !== id && !neighborIds.has(n.id));
    });
    document.querySelector("#detail-placeholder").hidden = true;
    document.querySelector("#detail-content").hidden = false;
    const panel = document.querySelector("#detail-panel"); panel.classList.add("open");
    const categoryLabel = document.querySelector("#detail-category");
    categoryLabel.textContent = category.label; categoryLabel.style.setProperty("--category-color", category.color);
    document.querySelector("#detail-title").textContent = node.label;
    document.querySelector("#detail-description").textContent = node.description;
    document.querySelector("#connection-count").textContent = neighborIds.size;
    const detailLinks = document.querySelector("#detail-links"); detailLinks.textContent = "";
    [...neighborIds].map(nodeId => nodeById.get(nodeId)).sort((a,b) => a.label.localeCompare(b.label)).forEach(neighbor => {
      const button = document.createElement("button"); button.type = "button"; button.className = "detail-link";
      button.style.setProperty("--dot", categories.get(neighbor.category).color);
      button.innerHTML = `<span class="dot" aria-hidden="true"></span><span>${neighbor.label}</span>`;
      button.addEventListener("click", () => selectNode(neighbor.id));
      detailLinks.appendChild(button);
    });
  }

  function clearSelection() {
    selectedId = null;
    nodes.forEach(n => n.el.classList.remove("selected", "dimmed"));
    links.forEach(l => l.el.classList.remove("highlighted", "dimmed"));
    document.querySelector("#detail-panel").classList.remove("open");
    document.querySelector("#detail-placeholder").hidden = false;
    document.querySelector("#detail-content").hidden = true;
  }

  function graphPoint(event) {
    const rect = svg.getBoundingClientRect();
    return { x: (event.clientX - rect.left - rect.width / 2 - transform.x) / transform.k, y: (event.clientY - rect.top - rect.height / 2 - transform.y) / transform.k };
  }

  function startNodeDrag(event, node) {
    event.stopPropagation();
    node.wasDragged = false;
    node.el.setPointerCapture(event.pointerId);
    const p = graphPoint(event);
    dragging = { node, pointerId: event.pointerId, dx: node.x - p.x, dy: node.y - p.y, startX: event.clientX, startY: event.clientY };
    node.fixed = true; alpha = .7; startSimulation();
  }

  graphPanel.addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch") return;
    touchPoints.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (touchPoints.size === 2) {
      const [a, b] = [...touchPoints.values()];
      pinch = { distance: Math.hypot(b.x-a.x,b.y-a.y), k: transform.k, centerX:(a.x+b.x)/2, centerY:(a.y+b.y)/2 };
      if (dragging) dragging.node.fixed = false;
      dragging = pan = null;
      graphPanel.classList.add("panning");
    }
  }, true);
  graphPanel.addEventListener("pointermove", event => {
    if (event.pointerType === "touch" && touchPoints.has(event.pointerId)) {
      touchPoints.set(event.pointerId, { x:event.clientX, y:event.clientY });
    }
    if (pinch && touchPoints.size >= 2) {
      const [a,b] = [...touchPoints.values()];
      const distance = Math.max(10,Math.hypot(b.x-a.x,b.y-a.y));
      const target = Math.max(.45,Math.min(2.8,pinch.k*distance/pinch.distance));
      zoomAt(pinch.centerX,pinch.centerY,target/transform.k);
    } else if (dragging && dragging.pointerId === event.pointerId) {
      const p = graphPoint(event); const n = dragging.node;
      n.x = p.x + dragging.dx; n.y = p.y + dragging.dy; n.vx = n.vy = 0;
      if (Math.hypot(event.clientX - dragging.startX, event.clientY - dragging.startY) > 4) n.wasDragged = true;
      render();
    } else if (pan && pan.pointerId === event.pointerId) {
      transform.x = pan.tx + event.clientX - pan.x; transform.y = pan.ty + event.clientY - pan.y; updateTransform();
    }
  });
  graphPanel.addEventListener("pointerup", event => { touchPoints.delete(event.pointerId); if(touchPoints.size<2)pinch=null; if (dragging && dragging.pointerId === event.pointerId) { dragging.node.fixed = false; dragging = null; } if (pan && pan.pointerId === event.pointerId) { pan = null; graphPanel.classList.remove("panning"); } if(!pinch)graphPanel.classList.remove("panning"); });
  graphPanel.addEventListener("pointercancel", event => { touchPoints.delete(event.pointerId); if (dragging) dragging.node.fixed = false; dragging = pan = pinch = null; graphPanel.classList.remove("panning"); });
  svg.addEventListener("pointerdown", event => { if (event.target === svg || event.target.closest("#links")) { pan = { pointerId:event.pointerId,x:event.clientX,y:event.clientY,tx:transform.x,ty:transform.y }; svg.setPointerCapture(event.pointerId); graphPanel.classList.add("panning"); clearSelection(); } });
  svg.addEventListener("wheel", event => { event.preventDefault(); zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * .0012)); }, { passive:false });
  svg.addEventListener("click", event => { if (event.target === svg) clearSelection(); });

  function zoomAt(clientX, clientY, factor) {
    const rect = svg.getBoundingClientRect();
    const px = clientX - rect.left - rect.width / 2, py = clientY - rect.top - rect.height / 2;
    const oldK = transform.k, newK = Math.max(.45, Math.min(2.8, oldK * factor));
    transform.x = px - (px - transform.x) * newK / oldK;
    transform.y = py - (py - transform.y) * newK / oldK;
    transform.k = newK; updateTransform();
  }
  function updateTransform() { viewport.setAttribute("transform", `translate(${svg.clientWidth/2 + transform.x} ${svg.clientHeight/2 + transform.y}) scale(${transform.k})`); }
  document.querySelector("#zoom-in").addEventListener("click", () => zoomAt(svg.clientWidth/2, svg.clientHeight/2, 1.25));
  document.querySelector("#zoom-out").addEventListener("click", () => zoomAt(svg.clientWidth/2, svg.clientHeight/2, .8));
  document.querySelector("#reset-view").addEventListener("click", () => { alpha=.55; startSimulation(); setTimeout(fitGraph,350); });
  document.querySelector("#close-detail").addEventListener("click", clearSelection);
  document.querySelector("#show-all").addEventListener("click", resetFilters);
  emptyState.querySelector("button").addEventListener("click", resetFilters);
  function resetFilters() { activeCategories.clear(); categories.forEach((_,id)=>activeCategories.add(id)); filterList.querySelectorAll(".filter").forEach(b=>b.setAttribute("aria-pressed","true")); search.value=""; applyFilters(); }
  search.addEventListener("input", applyFilters);
  document.addEventListener("keydown", event => { if (event.key === "/" && document.activeElement !== search) { event.preventDefault(); search.focus(); } if (event.key === "Escape") { if (document.activeElement === search && search.value) { search.value=""; applyFilters(); } else clearSelection(); } });

  function simulate() {
    const active = nodes.filter(visibleNode);
    const activeIds = new Set(active.map(n => n.id));
    links.forEach(link => {
      if (!activeIds.has(link.source) || !activeIds.has(link.target)) return;
      const dx=link.b.x-link.a.x, dy=link.b.y-link.a.y, dist=Math.max(1,Math.hypot(dx,dy));
      const desired=118, force=(dist-desired)*.016*alpha, fx=dx/dist*force, fy=dy/dist*force;
      if(!link.a.fixed){link.a.vx+=fx;link.a.vy+=fy} if(!link.b.fixed){link.b.vx-=fx;link.b.vy-=fy}
    });
    for(let i=0;i<active.length;i++){
      const a=active[i];
      for(let j=i+1;j<active.length;j++){
        const b=active[j], dx=b.x-a.x, dy=b.y-a.y;
        const xGap=(a.width+b.width)/2+18-Math.abs(dx), yGap=(a.height+b.height)/2+17-Math.abs(dy);
        if(xGap>0 && yGap>0){
          if(xGap<yGap){const push=xGap*.12*alpha*(dx>=0?1:-1);if(!a.fixed)a.vx-=push;if(!b.fixed)b.vx+=push}
          else{const push=yGap*.14*alpha*(dy>=0?1:-1);if(!a.fixed)a.vy-=push;if(!b.fixed)b.vy+=push}
        }
        const dist2=dx*dx+dy*dy+1, charge=18*alpha/dist2;
        if(!a.fixed){a.vx-=dx*charge;a.vy-=dy*charge}if(!b.fixed){b.vx+=dx*charge;b.vy+=dy*charge}
      }
    }
    active.forEach(n=>{if(!n.fixed){n.vx+=-n.x*.003*alpha;n.vy+=-n.y*.003*alpha;n.vx*=.8;n.vy*=.8;n.x+=n.vx;n.y+=n.vy}});
    // Resolve label rectangles directly after force integration. Multiple passes
    // remove collision cascades and guarantee readable wording at rest.
    for(let pass=0;pass<7;pass++){
      for(let i=0;i<active.length;i++)for(let j=i+1;j<active.length;j++){
        const a=active[i],b=active[j],dx=b.x-a.x,dy=b.y-a.y;
        const xOverlap=(a.width+b.width)/2+10-Math.abs(dx);
        const yOverlap=(a.height+b.height)/2+8-Math.abs(dy);
        if(xOverlap<=0||yOverlap<=0)continue;
        const movable=(a.fixed?0:1)+(b.fixed?0:1); if(!movable)continue;
        if(xOverlap<yOverlap){const amount=(xOverlap+1)/movable*(dx>=0?1:-1);if(!a.fixed)a.x-=amount;if(!b.fixed)b.x+=amount}
        else{const amount=(yOverlap+1)/movable*(dy>=0?1:-1);if(!a.fixed)a.y-=amount;if(!b.fixed)b.y+=amount}
      }
    }
    alpha*=.986; render();
    if(alpha>.018||dragging) animationId=requestAnimationFrame(simulate); else animationId=null;
  }
  function startSimulation(){if(!animationId)animationId=requestAnimationFrame(simulate)}
  function render(){nodes.forEach(n=>n.el.setAttribute("transform",`translate(${n.x.toFixed(2)} ${n.y.toFixed(2)})`));links.forEach(l=>{l.el.setAttribute("x1",l.a.x);l.el.setAttribute("y1",l.a.y);l.el.setAttribute("x2",l.b.x);l.el.setAttribute("y2",l.b.y)})}
  function fitGraph(){
    const active=nodes.filter(visibleNode); if(!active.length)return;
    const minX=Math.min(...active.map(n=>n.x-n.width/2-6)),maxX=Math.max(...active.map(n=>n.x+n.width/2+6));
    const minY=Math.min(...active.map(n=>n.y-n.height/2)),maxY=Math.max(...active.map(n=>n.y+n.height/2));
    const width=Math.max(1,maxX-minX),height=Math.max(1,maxY-minY),padding=54;
    const k=Math.max(.3,Math.min(1.15,Math.min((svg.clientWidth-padding*2)/width,(svg.clientHeight-padding*2)/height)));
    transform={x:-(minX+maxX)/2*k,y:-(minY+maxY)/2*k,k}; updateTransform();
  }
  new ResizeObserver(updateTransform).observe(graphPanel);
  updateTransform(); applyFilters(); setTimeout(fitGraph,900);
})();
