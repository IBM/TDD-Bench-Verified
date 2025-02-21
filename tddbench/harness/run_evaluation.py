from __future__ import annotations

import docker
import json
import resource
import traceback

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
import re
import ast
from cldk.analysis.python.treesitter import PythonSitter


cldk_python = PythonSitter()

DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"

from tddbench.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    INSTANCE_IMAGE_BUILD_DIR,
    KEY_INSTANCE_ID,
    RUN_EVALUATION_LOG_DIR,
)
from tddbench.harness.docker_utils import (
    remove_image,
    copy_to_container,
    exec_run_with_timeout,
    cleanup_container,
    list_images,
    should_remove,
    clean_images,
)
from tddbench.harness.docker_build import (
    BuildImageError,
    build_container,
    build_env_images,
    close_logger,
    setup_logger,
)
from tddbench.harness.grading import get_eval_report, get_logs_eval
from tddbench.harness.test_spec import make_test_spec, TestSpec
from tddbench.harness.utils import load_tddbench_dataset, str2bool



def calculate_coverage(filename, coverage, start_code_list, is_before):
    if filename.startswith("/"):
        filename = filename[1:]
    coverage = coverage.split("\n")


    #find the relevant line from the coverage file
    target_line = ""
    for line in coverage:
        if line.startswith(filename):
            target_line = line.strip()
            break
    while target_line.find("  ") != -1:
        target_line = target_line.replace("  "," ")

    missinglines = target_line.split(" ")[6:]    

    #making list of missingline:
    missing_lines = []
    for item in missinglines:
        item = item.replace(",","")
        if item.find("->") != -1 and item.find("exit") == -1:
            item=item.split('->')
            missing_lines.append(int(item[0])) 
            missing_lines.append(int(item[1]))  
        elif item.find("-")!=-1 and item.find("exit") == -1:
            item=item.split('-')
            for i in range(int(item[0]),int(item[1]) + 1):
                missing_lines.append(i)
        elif item.find("-")!=-1 and item.find("exit") != -1:
            item=item.split('-') 
            missing_lines.append(int(item[0]))          
        else:
            missing_lines.append(int(item))     


    #making list of changed line from patch
    changed_line=[]
    for i in range(len(start_code_list)): 
        start_code=start_code_list[i]
        start=start_code[0]
        code=start_code[1]
        code=code.split("\n")

        if is_before:
            j=0
            while j <len(code):
                if code[j].strip().startswith("+"):
                    del code[j]
                    continue
                j=j+1    
        else:
            j=0
            while j <len(code):   
                if code[j].strip().startswith("-"):
                    del code[j]
                    continue
                j=j+1 


        for j in range(0,len(code)):
            if is_before:
                if code[j].strip().startswith("---"):
                    continue
                if code[j].strip().startswith("-"):
                    temp=code[j].replace("-","")
                    if temp.strip()=="":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j+start)  
            else:
                if code[j].strip().startswith("+++"):
                    continue                
                if code[j].strip().startswith("+"):
                    temp=code[j].replace("+","")
                    if temp.strip()=="":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j+start)  

    print(changed_line)
    count_miss=0
    for item in changed_line:
        if item in missing_lines:
            count_miss=count_miss+1

    return len(changed_line), count_miss        



def get_class_functions(text):    
    classes = cldk_python.get_all_classes(module=text)
    class_and_method_names = [[klazz.class_name+'::'+method.method_name for method in klazz.methods] for klazz in classes]
    functions={}
    for sub_array in class_and_method_names:
        for element in sub_array:
            functions[element.split("::")[1]]=element.split("::")[0]
    return functions


def get_outer_functions(text):
    """
    Extracts the names of outer functions containing 'test' in their name.

    Args:
        text (str): The text to parse.

    Returns:
        list: A list of function names.
    """
    tree = ast.parse(text)

    functions = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node,ast.AsyncFunctionDef):
            if node.name.find("test") == -1:
                continue
            functions.append(node.name)

    return functions




def get_contributing_functions(test_patch):
    """
    Extracts the test functions that have been added or modified from the given test patch.

    Args:
        test_patch (str): The test patch to analyze.

    Returns:
        dict: A dictionary mapping file names to lists of modified test functions.
    """
    list_functions={}
    test_patch_segments=test_patch.split("+++ b")           

    for j in range(1,len(test_patch_segments)):
        focus_text=test_patch_segments[j]
        filename=test_patch_segments[j].split("\n")[0].strip()

        if filename.startswith("/"):
            filename=filename[1:]

        segments=focus_text.split("def test")[1:]
        for i in range(len(segments)):
            fbody=segments[i]
            fname="test"+segments[i].split("(")[0].strip()

            flines=fbody.split("\n")
            for ln in flines:
                if ln.strip().startswith("+"):
                    ln=ln.replace("+","")
                    ln=ln.replace("-","")
                    if ln.strip()=="":
                        continue
                    
                    if filename in list_functions:  
                        if fname not in list_functions[filename]:   
                            list_functions[filename].append(fname)
                            break
                    else:
                        list_functions[filename]=[]
                        list_functions[filename].append(fname)
                        break

    return list_functions 




def modify_eval(text,instance_id,fun2test):
    lines=text.split("\n")
    text=""
    for ln in lines:
        if (ln.find("coverage run")!=-1 or ln.find("tox --current-env -epy39 -v --")!=-1 or ln.find("./bin/test -C")!=-1) and len(fun2test)>0:
            segments=ln.split(" ")
            fcount=0
            for i in range(len(segments)-1,0,-1):
                fname=segments[i]
                if fname.find(".py")!=-1 or fname.find(".")!=-1: # . is for django
                    fcount=fcount+1 
                else:
                    break       
            remain=ln.split(" ")[0:len(segments)-fcount]
            command=""

            for item in remain:
                command=command+item+" "

            testcases=""

            if instance_id.find("django")!=-1:
                for item in fun2test:
                    if item.startswith("tests/"):
                        item=item[6:]
                    item=item.replace(".py","")
                    item=item.replace("::",".")
                    item=item.replace("/",".")
                    testcases=testcases+item+" "
        
                text=text+command+testcases.strip()+"\n" 
                
            elif instance_id.find("sympy")!=-1:
                funcnames={}
                for item in fun2test:
                    value=item.split(".py::")[0]+".py" 
                    
                    temp=item.split(".py::")[1]
                    if temp.find("::")!=-1:
                        key=temp.split("::")[-1].strip()
                    else:
                        key=temp

                    funcnames[key]=value
            
                for key in funcnames:
                    text=text+command.split(" --verbose ")[0]+" --verbose "+"-k "+"'"+key+"' "+command.split(" --verbose ")[1]+" "+funcnames[key]+"\n"   
            else:    
                for item in fun2test:
                    testcases=testcases+item+" "
                text=text+command+testcases.strip()+"\n" 
            
        else:
            text=text+ln+"\n"
    return text


class EvaluationError(Exception):
    def __init__(self, instance_id, message, logger):
        super().__init__(message)
        self.super_str = super().__str__()
        self.instance_id = instance_id
        self.log_file = logger.log_file
        self.logger = logger

    def __str__(self):
        return (
            f"Evaluation error for {self.instance_id}: {self.super_str}\n"
            f"Check ({self.log_file}) for more information."
        )


def run_instance(
        test_spec: TestSpec,
        pred: dict,
        rm_image: bool,
        force_rebuild: bool,
        client: docker.DockerClient,
        run_id: str,
        timeout: int | None = None,
    ):
    """
    Run a single instance with the given prediction.

    Args:
        test_spec (TestSpec): TestSpec instance
        pred (dict): Prediction w/ model_name_or_path, model_patch, instance_id
        rm_image (bool): Whether to remove the image after running
        force_rebuild (bool): Whether to force rebuild the image
        client (docker.DockerClient): Docker client
        run_id (str): Run ID
        timeout (int): Timeout for running tests
    """

    fun2test=[]
    instance_id = test_spec.instance_id
    model_name_or_path = pred.get("model_name_or_path", "None").replace("/", "__")
    model_name_or_path = model_name_or_path+"_initial"
    log_dir = RUN_EVALUATION_LOG_DIR / run_id / model_name_or_path / instance_id
    log_dir.mkdir(parents=True, exist_ok=True)

    # Link the image build dir in the log dir
    build_dir = INSTANCE_IMAGE_BUILD_DIR / test_spec.instance_image_key.replace(":", "__")
    image_build_link = log_dir / "image_build_dir"
    if not image_build_link.exists():
        try:
            # link the image build dir in the log dir
            image_build_link.symlink_to(build_dir.absolute(), target_is_directory=True)
        except:
            # some error, idk why
            pass
    log_file = log_dir / "run_instance.log"

    # Set up report file + logger
    report_path = log_dir / "report.json"
    if report_path.exists():
        return instance_id, json.loads(report_path.read_text())
    logger = setup_logger(instance_id, log_file)

    # Run the instance
    container = None

    try:
        # Build + start instance container (instance image should already be built)
        container = build_container(test_spec, client, run_id, logger, rm_image, force_rebuild)
        container.start()
        logger.info(f"Container for {instance_id} started: {container.id}")

        # Get git diff before running eval script
        git_diff_output_before = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )
        logger.info(f"Git diff before:\n{git_diff_output_before}")

        eval_file = Path(log_dir / "eval.sh")
        eval_file.write_text(test_spec.eval_script)
        logger.info(
            f"Eval script for {instance_id} written to {eval_file}; copying to container..."
        )

        test_patch=test_spec.test_patch
        contributing_functions=get_contributing_functions(test_patch)
        
        print(contributing_functions)

        #modify eval_file
        with open(eval_file,"r") as f:
            text=f.read()
        text=text.split("python3 -m pip install coverage")[0].strip()+"\n"

        with open(eval_file,"w") as f:
            text=f.write(text)
        copy_to_container(container, eval_file, Path("/eval.sh"))
        test_output, timed_out, total_runtime = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout)   

        for test_file in contributing_functions:
            command="cat "+test_file
            test_output, timed_out, total_runtime = exec_run_with_timeout(container, command , timeout)    
 
            class_func=get_class_functions(test_output)
            outer_func=get_outer_functions(test_output)

            for item in contributing_functions[test_file]:
                if item in class_func:
                    fun2test.append(test_file+"::"+class_func[item]+"::"+item)
                else:
                    if item in outer_func:
                        fun2test.append(test_file+"::"+item)
        print(fun2test) 

        reset_command="git clean -fd" 
        test_output, timed_out, total_runtime = exec_run_with_timeout(container, reset_command , timeout)
        close_logger(logger)


        print("Pointer 1")
        #############################################################################################
        # Set up logging directory
        instance_id = test_spec.instance_id
        model_name_or_path = pred.get("model_name_or_path", "None").replace("/", "__")
        model_name_or_path = model_name_or_path+"_before"
        log_dir = RUN_EVALUATION_LOG_DIR / run_id / model_name_or_path / instance_id
        log_dir.mkdir(parents=True, exist_ok=True)

        # Link the image build dir in the log dir
        build_dir = INSTANCE_IMAGE_BUILD_DIR / test_spec.instance_image_key.replace(":", "__")
        image_build_link = log_dir / "image_build_dir"
        if not image_build_link.exists():
            try:
                # link the image build dir in the log dir
                image_build_link.symlink_to(build_dir.absolute(), target_is_directory=True)
            except:
                # some error, idk why
                pass
        log_file = log_dir / "run_instance.log"

        # Set up report file + logger
        report_path = log_dir / "report.json"
        if report_path.exists():
            return instance_id, json.loads(report_path.read_text())
        logger = setup_logger(instance_id, log_file)


        # Get git diff before running eval script
        git_diff_output_before = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )
        logger.info(f"Git diff before:\n{git_diff_output_before}")

        eval_file = Path(log_dir / "eval.sh")
        eval_file.write_text(test_spec.eval_script)

        #modify eval_file
        with open(eval_file,"r") as f:
            text=f.read()

        text=modify_eval(text,instance_id,fun2test)
               
        with open(eval_file,"w") as f:
            text=f.write(text)

        logger.info(
            f"Eval script for {instance_id} written to {eval_file}; copying to container..."
        )
        copy_to_container(container, eval_file, Path("/eval.sh"))

        # Run eval script, write output to logs
        test_output, timed_out, total_runtime = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout)
        test_output_path = log_dir / "test_coverage_combine.txt"
        logger.info(f'Test runtime: {total_runtime:_.2f} seconds')
        with open(test_output_path, "w") as f:
            f.write(test_output)
            logger.info(f"Test output for {instance_id} written to {test_output_path}")
            if timed_out:
                f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")
                raise EvaluationError(
                    instance_id,
                    f"Test timed out after {timeout} seconds.",
                    logger,
                )
        # divide test_output and coverage
        
        with open(test_output_path,"r") as f:
            test_coverage = f.read()
            test_coverage = test_coverage.split("+ coverage report")
            test=test_coverage[0]
            coverage=test_coverage[1]

        test_output_path=log_dir / "test_output.txt"   
        coverage_output_path=log_dir / "test_coverage.txt"

        with open(test_output_path, "w") as f:
            f.write(test)
        with open(coverage_output_path, "w") as f:
            f.write(coverage)

        patch_text=pred["model_patch"]


        total_change_before=0
        total_miss_before=0

        patch_text_segmets=patch_text.split("+++ b")    


        for j in range(1,len(patch_text_segmets)):
            focus_text=patch_text_segmets[j]
            filename=patch_text_segmets[j].split("\n")[0].strip()
            segement_count=int(len(focus_text.split("@@"))/2)
            start_code_list=[]
            for i in range(0,segement_count):
                before_lines=focus_text.split("@@")[2*i+1].strip().split(" ")[0]
                start=abs(int((before_lines.split(",")[0])))-1
                code=focus_text.split("@@")[2*i+2]
                start_code=(start,code)
                start_code_list.append(start_code)
            count_change, count_miss=calculate_coverage(filename, coverage, start_code_list, True)
            
            total_change_before+=count_change
            total_miss_before+=count_miss
            



        # Get git diff after running eval script
        git_diff_output_after = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )

        # Check if git diff changed after running eval script
        logger.info(f"Git diff after:\n{git_diff_output_after}")
        if git_diff_output_after != git_diff_output_before:
            logger.info(f"Git diff changed after running eval script")

        test_result_before,_=get_logs_eval(test_output_path)
        print('-------------------------------Before Patch-------------------------------')
        print(test_result_before)
        print('---------------------------------------------------------------------------------\n\n\n')

        reset_command="git clean -fd" 
        test_output, timed_out, total_runtime = exec_run_with_timeout(container, reset_command , timeout)
        close_logger(logger)
        

        print("Pointer 2")
        #############################################################################################
        # Set up logging directory
        instance_id = test_spec.instance_id
        model_name_or_path = pred.get("model_name_or_path", "None").replace("/", "__")
        log_dir = RUN_EVALUATION_LOG_DIR / run_id / model_name_or_path / instance_id
        log_dir.mkdir(parents=True, exist_ok=True)

        # Link the image build dir in the log dir
        build_dir = INSTANCE_IMAGE_BUILD_DIR / test_spec.instance_image_key.replace(":", "__")
        image_build_link = log_dir / "image_build_dir"
        if not image_build_link.exists():
            try:
                # link the image build dir in the log dir
                image_build_link.symlink_to(build_dir.absolute(), target_is_directory=True)
            except:
                # some error, idk why
                pass
        log_file = log_dir / "run_instance.log"

        # Set up report file + logger
        report_path = log_dir / "report.json"
        if report_path.exists():
            return instance_id, json.loads(report_path.read_text())
        logger = setup_logger(instance_id, log_file)

        # Copy model prediction as patch file to container
        patch_file = Path(log_dir / "patch.diff")
        patch_file.write_text(pred["model_patch"] or "")
        logger.info(
            f"Intermediate patch for {instance_id} written to {patch_file}, now applying to container..."
        )
        copy_to_container(container, patch_file, Path("/tmp/patch.diff"))

        # Attempt to apply patch to container
        val = container.exec_run(
            "git apply --allow-empty -v /tmp/patch.diff",
            workdir="/testbed",
            user="root",
        )
        if val.exit_code != 0:
            logger.info(f"Failed to apply patch to container, trying again...")
            
            # try "patch --batch --fuzz=5 -p1 -i {patch_path}" to try again
            val = container.exec_run(
                "patch --batch --fuzz=5 -p1 -i /tmp/patch.diff",
                workdir="/testbed",
                user="root",
            )
            if val.exit_code != 0:
                logger.info(f"{APPLY_PATCH_FAIL}:\n{val.output.decode('utf-8')}")
                raise EvaluationError(
                    instance_id,
                    f"{APPLY_PATCH_FAIL}:\n{val.output.decode('utf-8')}",
                    logger,
                )
            else:
                logger.info(f"{APPLY_PATCH_PASS}:\n{val.output.decode('utf-8')}")
        else:
            logger.info(f"{APPLY_PATCH_PASS}:\n{val.output.decode('utf-8')}")

        # Get git diff before running eval script
        git_diff_output_before = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )
        logger.info(f"Git diff before:\n{git_diff_output_before}")

        eval_file = Path(log_dir / "eval.sh")
        eval_file.write_text(test_spec.eval_script)



        #modify eval_file
        with open(eval_file,"r") as f:
            text=f.read()

        text=modify_eval(text,instance_id,fun2test)
               
               
        with open(eval_file,"w") as f:
            text=f.write(text)


        logger.info(
            f"Eval script for {instance_id} written to {eval_file}; copying to container..."
        )
        copy_to_container(container, eval_file, Path("/eval.sh"))

        # Run eval script, write output to logs
        test_output, timed_out, total_runtime = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout)
        test_output_path = log_dir / "test_coverage_combine.txt"
        logger.info(f'Test runtime: {total_runtime:_.2f} seconds')
        with open(test_output_path, "w") as f:
            f.write(test_output)
            logger.info(f"Test output for {instance_id} written to {test_output_path}")
            if timed_out:
                f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")
                raise EvaluationError(
                    instance_id,
                    f"Test timed out after {timeout} seconds.",
                    logger,
                )
        # divide test_output and coverage
        with open(test_output_path,"r") as f:
            test_coverage = f.read()
            test_coverage = test_coverage.split("+ coverage report")
            test=test_coverage[0]
            coverage=test_coverage[1]

        test_output_path=log_dir / "test_output.txt"   
        coverage_output_path=log_dir / "test_coverage.txt"

        with open(test_output_path, "w") as f:
            f.write(test)
        with open(coverage_output_path, "w") as f:
            f.write(coverage)

        patch_text=pred["model_patch"]
        
        patch_text_segmets=patch_text.split("+++ b")    


        total_change_after=0
        total_miss_after=0

        for j in range(1,len(patch_text_segmets)):
            focus_text=patch_text_segmets[j]
            filename=patch_text_segmets[j].split("\n")[0].strip()
            segement_count=int(len(focus_text.split("@@"))/2)
            start_code_list=[]
            for i in range(0,segement_count):
                after_lines=focus_text.split("@@")[2*i+1].strip().split(" ")[1]
                start=abs(int((after_lines.split(",")[0])))-1
                code=focus_text.split("@@")[2*i+2]
                start_code=(start,code)
                start_code_list.append(start_code)
            count_change, count_miss=calculate_coverage(filename, coverage, start_code_list, False)
            
            total_change_after+=count_change
            total_miss_after+=count_miss
            
        # Get git diff after running eval script
        git_diff_output_after = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )

        # Check if git diff changed after running eval script
        logger.info(f"Git diff after:\n{git_diff_output_after}")
        if git_diff_output_after != git_diff_output_before:
            logger.info(f"Git diff changed after running eval script")

        test_result,_=get_logs_eval(test_output_path)

        total_change=total_change_before+total_change_after
        total_miss=total_miss_before+total_miss_after


        if total_change==0:
            cov_score=0
        else:
            cov_score =(total_change-total_miss) / total_change * 1.0    
        



        print('-------------------------------After Patch-------------------------------')
        print(test_result)
        print('--------------------------------------------------------------------------------\n\n\n')

        print('-------------------------------Coverage-----------------------------------------')
        print(cov_score)
        print('--------------------------------------------------------------------------------\n\n\n')


        report={}
        report[instance_id]={}

        report[instance_id]['contributing_functions']=fun2test
        report[instance_id]['test_before_patch']=test_result_before
        report[instance_id]['test_after_patch']=test_result
        report[instance_id]['total_changed']=total_change
        report[instance_id]['total_missed']=total_miss
        report[instance_id]['cov_score']=cov_score


        fail_before=0
        for item in test_result_before:
            if test_result_before[item]=='SKIP':
                continue
            if test_result_before[item]=='FAILED' or test_result_before[item]=='ERROR':
                fail_before=1
                break

        pass_after=0
        for item in test_result:
            if test_result[item]=='PASSED':
                pass_after=1
            if test_result[item]=='SKIP':
                continue
            if test_result[item]=='FAILED' or test_result[item]=='ERROR':
                pass_after=0
                break

        if instance_id.lower().find("sympy")!=-1:
            report[instance_id]['final_score']=fail_before*pass_after
        else:
            report[instance_id]['final_score']=cov_score*fail_before*pass_after   


        if report[instance_id]['final_score']>0:
            report[instance_id]['resolved']=True
        else:
            report[instance_id]['resolved']=False  


        print('-------------------------------Final Report--------------------------------------')      
        print(report)
        print('---------------------------------------------------------------------------------\n\n\n')

        with open(report_path, "w") as f:
            f.write(json.dumps(report, indent=4))

    except EvaluationError as e:
        error_msg = traceback.format_exc()
        logger.info(error_msg)
        print(e)
    except BuildImageError as e:
        error_msg = traceback.format_exc()
        logger.info(error_msg)
        print(e)
    except Exception as e:
        error_msg = (f"Error in evaluating model for {instance_id}: {e}\n"
                     f"{traceback.format_exc()}\n"
                     f"Check ({logger.log_file}) for more information.")
        logger.error(error_msg)
    finally:
        # Remove instance container + image, close logger
        cleanup_container(client, container, logger)
        if rm_image:
            remove_image(client, test_spec.instance_image_key, logger)
        close_logger(logger)

    return


def run_instances(
        predictions: dict,
        instances: list,
        cache_level: str,
        clean: bool,
        force_rebuild: bool,
        max_workers: int,
        run_id: str,
        timeout: int,
    ):
    """
    Run all instances for the given predictions in parallel.

    Args:
        predictions (dict): Predictions dict generated by the model
        instances (list): List of instances
        cache_level (str): Cache level
        clean (bool): Clean images above cache level
        force_rebuild (bool): Force rebuild images
        max_workers (int): Maximum number of workers
        run_id (str): Run ID
        timeout (int): Timeout for running tests
    """
    client = docker.from_env()
    test_specs = list(map(make_test_spec, instances))

    # print number of existing instance images
    instance_image_ids = {x.instance_image_key for x in test_specs}
    existing_images = {
        tag for i in client.images.list(all=True)
        for tag in i.tags if tag in instance_image_ids
    }
    if not force_rebuild and len(existing_images):
        print(f"Found {len(existing_images)} existing instance images. Will reuse them.")

    # run instances in parallel
    print(f"Running {len(instances)} instances...")
    with tqdm(total=len(instances), smoothing=0) as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create a future for running each instance
            futures = {
                executor.submit(
                    run_instance,
                    test_spec,
                    predictions[test_spec.instance_id],
                    should_remove(
                        test_spec.instance_image_key,
                        cache_level,
                        clean,
                        existing_images,
                    ),
                    force_rebuild,
                    client,
                    run_id,
                    timeout,
                ): None
                for test_spec in test_specs
            }
            # Wait for each future to complete
            for future in as_completed(futures):
                pbar.update(1)
                try:
                    # Update progress bar, check if instance ran successfully
                    future.result()
                except Exception as e:
                    traceback.print_exc()
                    continue
    print("All instances run.")


def get_dataset_from_preds(
        dataset_name: str,
        split: str,
        instance_ids: list,
        predictions: dict,
        patch_model_name: str,
        run_id: str,
        exclude_completed: bool = True
    ):
    """
    Return only instances that have predictions and are in the dataset.
    If instance_ids is provided, only return instances with those IDs.
    If exclude_completed is True, only return instances that have not been run yet.
    """
    # load dataset
    dataset = load_tddbench_dataset(dataset_name, split)
    dataset_ids = {i[KEY_INSTANCE_ID] for i in dataset}

    if instance_ids:
        # check that all instance IDs have predictions
        missing_preds = set(instance_ids) - set(predictions.keys())
        if missing_preds:
            print(f"Warning: Missing predictions for {len(missing_preds)} instance IDs.")
    
    # check that all prediction IDs are in the dataset
    prediction_ids = set(predictions.keys())
    if prediction_ids - dataset_ids:
        raise ValueError(
            (
                "Some prediction IDs not found in dataset!"
                f"\nMissing IDs:\n{' '.join(prediction_ids - dataset_ids)}"
            )
        )
    if instance_ids:
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] in instance_ids]

    # check which instance IDs have already been run
    completed_ids = set()
    for instance in dataset:
        if instance[KEY_INSTANCE_ID] not in prediction_ids:
            # skip instances without predictions
            continue
        prediction = predictions[instance[KEY_INSTANCE_ID]]
        report_file = (
            RUN_EVALUATION_LOG_DIR
            / run_id
            / patch_model_name
            / prediction[KEY_INSTANCE_ID]
            / "report.json"
        )
        if report_file.exists():
            completed_ids.add(instance[KEY_INSTANCE_ID])

    if completed_ids and exclude_completed:
        # filter dataset to only instances that have not been run
        print(f"{len(completed_ids)} instances already run, skipping...")
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] not in completed_ids]

    empty_patch_ids = {k for k, v in predictions.items() if v["model_patch"] == "" or v["model_patch"] is None}

    # filter dataset to only instances with predictions
    dataset = [i for i in dataset if i[KEY_INSTANCE_ID] in prediction_ids and i[KEY_INSTANCE_ID] not in empty_patch_ids]
    return dataset


def make_run_report(
        patch_model_name,
        predictions: dict,
        full_dataset: list,
        client: docker.DockerClient,
        run_id: str
    ) -> Path:
    """
    Make a final evaluation and run report of the instances that have been run.
    Also reports on images and containers that may still running!
    Args:
        predictions (dict): Predictions dict generated by the model
        full_dataset (list): List of all instances
        client (docker.DockerClient): Docker client
        run_id (str): Run ID
    
    Returns:
        Path to report file
    """
    # instantiate sets to store IDs of different outcomes
    completed_ids = set()
    resolved_ids = set()
    error_ids = set()
    unstopped_containers = set()
    unremoved_images = set()
    unresolved_ids = set()
    incomplete_ids = set()
    # get instances with empty patches
    empty_patch_ids = set()

    score=0.0

    # iterate through dataset and check if the instance has been run
    for instance in full_dataset:
        instance_id = instance[KEY_INSTANCE_ID]
        if instance_id not in predictions:
            # skip instances without 
            incomplete_ids.add(instance_id)
            continue
        prediction = predictions[instance_id]
        if prediction.get("model_patch", None) in ["", None]:
            empty_patch_ids.add(instance_id)
            continue
        report_file = (
            RUN_EVALUATION_LOG_DIR
            / run_id
            / patch_model_name
            / prediction[KEY_INSTANCE_ID]
            / "report.json"
        )
        
        if report_file.exists():
            # If report file exists, then the instance has been run
            completed_ids.add(instance_id)
            report = json.loads(report_file.read_text())
            if report[instance_id]["resolved"]:
                # Record if the instance was resolved
                resolved_ids.add(instance_id)
            else:
                unresolved_ids.add(instance_id)
            score=score+float(report[instance_id]["final_score"])

        else:
            # Otherwise, the instance was not run successfully
            error_ids.add(instance_id)

    # get remaining images and containers
    images = list_images(client)
    test_specs = list(map(make_test_spec, full_dataset))
    for spec in test_specs:
        image_name = spec.instance_image_key
        if image_name in images:
            unremoved_images.add(image_name)
    containers = client.containers.list(all=True)
    for container in containers:
        if run_id in container.name:
            unstopped_containers.add(container.name)

    # print final report
    dataset_ids = {i[KEY_INSTANCE_ID] for i in full_dataset}
    print(f"Total instances: {len(full_dataset)}")
    print(f"Instances submitted: {len(set(predictions.keys()) & dataset_ids)}")
    print(f"Instances completed: {len(completed_ids)}")
    print(f"Instances incomplete: {len(incomplete_ids)}")
    print(f"Instances resolved: {len(resolved_ids)}")
    print(f"Instances unresolved: {len(unresolved_ids)}")
    print(f"Instances with empty patches: {len(empty_patch_ids)}")
    print(f"Instances with errors: {len(error_ids)}")
    print(f"Unstopped containers: {len(unstopped_containers)}")
    print(f"Unremoved images: {len(unremoved_images)}")
    print(f"Final score: {score/len(full_dataset)}")

    # write report to file
    report = {
        "total_instances": len(full_dataset),
        "submitted_instances": len(predictions),
        "completed_instances": len(completed_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "empty_patch_instances": len(empty_patch_ids),
        "error_instances": len(error_ids),
        "unstopped_instances": len(unstopped_containers),
        "completed_ids": list(sorted(completed_ids)),
        "incomplete_ids": list(sorted(incomplete_ids)),
        "empty_patch_ids": list(sorted(empty_patch_ids)),
        "submitted_ids": list(sorted(predictions.keys())),
        "resolved_ids": list(sorted(resolved_ids)),
        "unresolved_ids": list(sorted(unresolved_ids)),
        "error_ids": list(sorted(error_ids)),
        "unstopped_containers": list(sorted(unstopped_containers)),
        "unremoved_images": list(sorted(unremoved_images)),
        "final_score": score/len(full_dataset),
        "schema_version": 2,
    }
    report_file = Path(
        list(predictions.values())[0]["model_name_or_path"].replace("/", "__")
        + f".{run_id}"
        + ".json"
    )
    with open(report_file, "w") as f:
        print(json.dumps(report, indent=4), file=f)
    print(f"Report written to {report_file}")
    return report_file



def get_golden_patch(dataset_name: str, split: str):
    """
    Get golden patch for the given dataset and split.
    """
    dataset = load_tddbench_dataset(dataset_name, split)
    return [
        {
            KEY_INSTANCE_ID: datum[KEY_INSTANCE_ID],
            "model_patch": datum["patch"],
            "model_name_or_path": "gold",
        } for datum in dataset
    ]

def get_gold_predictions(dataset_name: str, split: str):
    """
    Get gold predictions for the given dataset and split.
    """
    dataset = load_tddbench_dataset(dataset_name, split)
    return [
        {
            KEY_INSTANCE_ID: datum[KEY_INSTANCE_ID],
            "model_patch": datum["test_patch"],
            "model_name_or_path": "gold",
        } for datum in dataset
    ]


def main(
        dataset_name: str,
        split: str,
        instance_ids: list,
        patch_path: str,
        predictions_path: str,
        max_workers: int,
        force_rebuild: bool,
        cache_level: str,
        clean: bool,
        open_file_limit: int,
        run_id: str,
        timeout: int,
    ):
    """
    Run evaluation harness for the given dataset and predictions.
    """
    # set open file limit
    assert len(run_id) > 0, "Run ID must be provided"
    resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))
    client = docker.from_env()

    # load predictions as map of instance_id to prediction
    if predictions_path == 'gold':
        print("Using gold predictions - ignoring predictions_path")
        predictions = get_gold_predictions(dataset_name, split)
    else:
        if predictions_path.endswith(".json"):
            with open(predictions_path, "r") as f:
                predictions = json.load(f)
        elif predictions_path.endswith(".jsonl"):
            with open(predictions_path, "r") as f:
                predictions = [json.loads(line) for line in f]
        else:
            raise ValueError("Predictions path must be \"gold\", .json, or .jsonl")

        for pred in predictions:
            pred["model_name_or_path"]=predictions_path


    if patch_path == 'gold':
        print("Using gold patch predictions")
        patch_model_name = 'gold'
        patches = get_golden_patch(dataset_name, split)
    else:
        patch_p: Path = Path(patch_path)
        if patch_p.is_dir():
            patch_model_name = patch_p.parts[-1]
            outsw = Path(patch_p, 'output.swebench.jsonl')
            if outsw.is_file():
                print(f"Using patch prediction: {outsw}")
                patch_p = outsw
            else:
                raise ValueError(f'Given patch path given a directory ({patch_p}) without an output.swebench.jsonl file')
        else:
            # given a file, we will still use the folder name
            patch_model_name = patch_p.parts[-2]

        if patch_p.suffix == ".json":
            with open(patch_p, "r") as f:
                patches = json.load(f)
        elif patch_p.suffix == ".jsonl":
            with open(patch_p, "r") as f:
                patches = [json.loads(line) for line in f]
        else:
            raise ValueError("Patch predictions path must be \"gold\", .json, or .jsonl")

        for pred in patches:
            pred["model_name_or_path"]=patch_model_name


    predictions = {pred[KEY_INSTANCE_ID]: pred for pred in predictions}
    patches = {patch[KEY_INSTANCE_ID]: patch for patch in patches}



    # get dataset from predictions
    dataset = get_dataset_from_preds(dataset_name, split, instance_ids, predictions, patch_model_name, run_id)

    full_dataset = load_tddbench_dataset(dataset_name, split, instance_ids)

    for i in range(0,len(dataset)): 
        dataset[i]['test_patch']=predictions[dataset[i]['instance_id']]['model_patch']  

    existing_images = list_images(client)
    print(f"Running {len(dataset)} unevaluated instances...")
    if not dataset:
        print("No instances to run.")
    else:
        # build environment images + run instances
        build_env_images(client, dataset, force_rebuild, max_workers)
        run_instances(patches, dataset, cache_level, clean, force_rebuild, max_workers, run_id, timeout)

    # clean images + make final report
    clean_images(client, existing_images, cache_level, clean)
    make_run_report(patch_model_name, predictions, full_dataset, client, run_id)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--dataset_name", default="TDD_Bench.json", type=str, help="Name of dataset or path to JSON file.")
    parser.add_argument("--split", type=str, default="test", help="Split of the dataset")
    parser.add_argument("--instance_ids", nargs="+", type=str, help="Instance IDs to run (space separated)")
    parser.add_argument("--patch_path", default="gold", type=str, help="Path to patches predictions file - if 'gold', uses gold patch")
    parser.add_argument("--predictions_path", type=str, help="Path to predictions file - if 'gold', uses gold predictions", required=True)
    parser.add_argument("--max_workers", type=int, default=4, help="Maximum number of workers (should be <= 75%% of CPU cores)")
    parser.add_argument("--open_file_limit", type=int, default=4096, help="Open file limit")
    parser.add_argument(
        "--timeout", type=int, default=1_800, help="Timeout (in seconds) for running tests for each instance"
        )
    parser.add_argument(
        "--force_rebuild", type=str2bool, default=False, help="Force rebuild of all images"
    )
    parser.add_argument(
        "--cache_level",
        type=str,
        choices=["none", "base", "env", "instance"],
        help="Cache level - remove images above this level",
        default="env",
    )
    # if clean is true then we remove all images that are above the cache level
    # if clean is false, we only remove images above the cache level if they don't already exist
    parser.add_argument(
        "--clean", type=str2bool, default=False, help="Clean images above cache level"
    )
    parser.add_argument("--run_id", type=str, required=True, help="Run ID - identifies the run")
    args = parser.parse_args()

    main(**vars(args))
