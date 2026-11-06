async function go() {
  await openManager();
  const hint = document.getElementById("state");
  if (hint) hint.textContent = "已切换到 Moltskin 控制台";
}

document.getElementById("open").addEventListener("click", go);
go();
