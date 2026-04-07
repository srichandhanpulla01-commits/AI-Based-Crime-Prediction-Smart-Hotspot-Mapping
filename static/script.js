function roundRectPath(ctx, x, y, width, height, radius) {
    const safeRadius = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + safeRadius, y);
    ctx.lineTo(x + width - safeRadius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
    ctx.lineTo(x + width, y + height - safeRadius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
    ctx.lineTo(x + safeRadius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
    ctx.lineTo(x, y + safeRadius);
    ctx.quadraticCurveTo(x, y, x + safeRadius, y);
    ctx.closePath();
}

function getCanvasMetrics(canvas) {
    if (!canvas) return null;
    const displayWidth = canvas.clientWidth || canvas.offsetWidth;
    const displayHeight = canvas.clientHeight || canvas.offsetHeight || 260;
    if (!displayWidth || !displayHeight) return null;

    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(displayWidth * ratio);
    canvas.height = Math.floor(displayHeight * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, displayWidth, displayHeight };
}

function showSection(id, trigger) {
    const sections = document.getElementsByClassName("section");
    const navButtons = document.getElementsByClassName("nav-button");

    for (let i = 0; i < sections.length; i += 1) sections[i].classList.remove("active-section");
    for (let i = 0; i < navButtons.length; i += 1) navButtons[i].classList.remove("active");

    document.getElementById(id).classList.add("active-section");
    if (trigger) trigger.classList.add("active");
    if (id === "crime") window.setTimeout(renderGraphs, 80);
}

function scrollToGraph(graphId) {
    const target = document.getElementById(graphId);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
}

function attachRoutePickerListener() {
    window.addEventListener("message", (event) => {
        if (!event.data || event.data.type !== "route-picker") return;
        const field = document.querySelector(`input[name="${event.data.field}"]`);
        if (field) field.value = event.data.value;
    });
}

function drawHorizontalBarChart(canvasId, labels, values, colorA, colorB) {
    const canvas = document.getElementById(canvasId);
    const metrics = getCanvasMetrics(canvas);
    if (!metrics || !labels.length || !values.length) return;

    const { ctx, displayWidth, displayHeight } = metrics;
    const leftLabelSpace = 150;
    const padding = { top: 18, right: 26, bottom: 16, left: leftLabelSpace };
    const chartWidth = displayWidth - padding.left - padding.right;
    const rowHeight = Math.max(24, (displayHeight - padding.top - padding.bottom) / labels.length);
    const barHeight = Math.min(24, rowHeight * 0.68);
    const maxValue = Math.max(...values, 1);

    ctx.clearRect(0, 0, displayWidth, displayHeight);
    ctx.font = "12px Segoe UI";
    ctx.textBaseline = "middle";

    labels.forEach((label, index) => {
        const y = padding.top + index * rowHeight + (rowHeight - barHeight) / 2;
        const barWidth = (values[index] / maxValue) * chartWidth;
        const gradient = ctx.createLinearGradient(padding.left, y, padding.left + barWidth, y);
        gradient.addColorStop(0, colorA);
        gradient.addColorStop(1, colorB);

        ctx.fillStyle = "rgba(31, 79, 130, 0.08)";
        roundRectPath(ctx, padding.left, y, chartWidth, barHeight, 10);
        ctx.fill();

        ctx.fillStyle = gradient;
        roundRectPath(ctx, padding.left, y, Math.max(barWidth, 8), barHeight, 10);
        ctx.fill();

        ctx.fillStyle = "#33485f";
        ctx.textAlign = "right";
        ctx.fillText(label, padding.left - 12, y + barHeight / 2);

        ctx.textAlign = "left";
        ctx.fillStyle = "#132235";
        ctx.fillText(String(values[index]), padding.left + Math.max(barWidth, 8) + 8, y + barHeight / 2);
    });
}

function drawLineChart(canvasId, labels, values, lineColor) {
    const canvas = document.getElementById(canvasId);
    const metrics = getCanvasMetrics(canvas);
    if (!metrics || !labels.length || !values.length) return;

    const { ctx, displayWidth, displayHeight } = metrics;
    const padding = { top: 24, right: 20, bottom: 56, left: 36 };
    const maxValue = Math.max(...values, 1);
    const chartWidth = displayWidth - padding.left - padding.right;
    const chartHeight = displayHeight - padding.top - padding.bottom;
    const stepX = labels.length > 1 ? chartWidth / (labels.length - 1) : chartWidth / 2;

    ctx.clearRect(0, 0, displayWidth, displayHeight);
    ctx.strokeStyle = "rgba(31, 79, 130, 0.14)";
    for (let i = 0; i <= 4; i += 1) {
        const y = padding.top + (chartHeight / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(displayWidth - padding.right, y);
        ctx.stroke();
    }

    ctx.beginPath();
    values.forEach((value, index) => {
        const x = padding.left + stepX * index;
        const y = padding.top + chartHeight - (value / maxValue) * chartHeight;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 3;
    ctx.stroke();

    values.forEach((value, index) => {
        const x = padding.left + stepX * index;
        const y = padding.top + chartHeight - (value / maxValue) * chartHeight;
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = lineColor;
        ctx.stroke();

        ctx.fillStyle = "#132235";
        ctx.textAlign = "center";
        ctx.font = "12px Segoe UI";
        ctx.fillText(String(value), x, y - 12);
        ctx.fillStyle = "#4d6278";
        ctx.fillText(labels[index], x, displayHeight - 18);
    });
}

function renderGraphs() {
    const dataNode = document.getElementById("chart-data");
    if (!dataNode) return;

    const chartData = JSON.parse(dataNode.textContent);
    drawHorizontalBarChart("crimeChart", chartData.crimeLabels, chartData.crimeValues, "#1f4f82", "#4ea0e8");
    drawLineChart("timeChart", chartData.timeLabels, chartData.timeValues, "#b42318");
    drawHorizontalBarChart("stationChart", chartData.stationLabels, chartData.stationValues, "#16794a", "#33a06f");
    drawLineChart("trendChart", chartData.trendLabels, chartData.trendValues, "#b7791f");
}

window.addEventListener("load", () => {
    attachRoutePickerListener();
    renderGraphs();
    window.setTimeout(renderGraphs, 120);
});

window.addEventListener("resize", () => {
    window.setTimeout(renderGraphs, 100);
});
